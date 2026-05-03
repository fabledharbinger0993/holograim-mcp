"""
Holographic Distributed Coding (HDC) layer.
Uses FFT-based circular convolution for binding.
All errors are caught and logged — this layer never crashes the MCP server.
"""
import logging
import os
from typing import Any, Optional
import numpy as np
from config import COMPOSITE_PATH, HDC_DIMENSION

logger = logging.getLogger(__name__)

_composite: Optional[np.ndarray] = None
_composite_dirty = False


def _load_composite() -> np.ndarray:
    global _composite
    if _composite is None:
        if os.path.exists(COMPOSITE_PATH):
            try:
                _composite = np.load(COMPOSITE_PATH)
            except Exception as e:
                logger.warning(f"HDC: failed to load composite, starting fresh: {e}")
                _composite = np.zeros(HDC_DIMENSION, dtype=np.float64)
        else:
            _composite = np.zeros(HDC_DIMENSION, dtype=np.float64)
    return _composite


def _save_composite() -> None:
    try:
        np.save(COMPOSITE_PATH, _load_composite())
    except Exception as e:
        logger.warning(f"HDC: failed to save composite: {e}")


def _random_hv(seed: Optional[int] = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(HDC_DIMENSION)
    return v / (np.linalg.norm(v) + 1e-12)


def _encode(text: str) -> np.ndarray:
    seed = int.from_bytes(text.encode("utf-8")[:8].ljust(8, b"\x00"), "big") % (2**31)
    return _random_hv(seed)


def _bind(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """FFT-based circular convolution binding operation."""
    return np.roll(np.real(np.fft.ifft(np.fft.fft(a) * np.fft.fft(b))), 1)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


def add_to_holographic(mem_id: str, content: str) -> bool:
    """Encode content and superpose into composite. Returns True on success."""
    try:
        composite = _load_composite()
        mem_hv = _encode(content)
        id_hv = _encode(mem_id)
        bound = _bind(id_hv, mem_hv)
        composite += bound
        _composite = composite  # update module-level ref
        globals()["_composite"] = composite
        _save_composite()
        return True
    except Exception as e:
        logger.error(f"HDC add_to_holographic error: {e}")
        return False


def query_holographic(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """
    Query the holographic composite by probing with query vector.
    Returns similarity scores — useful as a re-ranking signal.
    Note: without a stored ID registry, we return the raw composite similarity
    as a single aggregate score.
    """
    try:
        composite = _load_composite()
        if np.all(composite == 0):
            return []
        query_hv = _encode(query)
        sim = _cosine_sim(query_hv, composite)
        return [{"holographic_similarity": round(sim, 4), "composite_norm": round(float(np.linalg.norm(composite)), 4)}]
    except Exception as e:
        logger.error(f"HDC query_holographic error: {e}")
        return []
