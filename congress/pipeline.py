"""
Paradigm → Congress → Ego pipeline using local Ollama.
Never fabricates responses — surfaces errors explicitly.
"""
import json
import logging
from typing import Any, Optional
import urllib.request
import urllib.error

from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_FALLBACK
from congress.prompts import (
    PARADIGM_PROMPT,
    ADVOCATE_PROMPT,
    SKEPTIC_PROMPT,
    SYNTHESIZER_PROMPT,
    ETHICS_PROMPT,
    EGO_PROMPT,
)
from db.queries import get_beliefs, get_memories

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    pass


def _ollama_generate(
    prompt: str,
    system: str,
    model: str,
) -> str:
    """Call Ollama /api/generate. Returns text or raises OllamaError."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except urllib.error.URLError as e:
        raise OllamaError(f"Ollama unreachable at {OLLAMA_HOST}: {e}")
    except Exception as e:
        raise OllamaError(f"Ollama request failed: {e}")


def _call_with_fallback(prompt: str, system: str, model: str) -> str:
    try:
        return _ollama_generate(prompt, system, model)
    except OllamaError:
        logger.warning(f"Primary model {model} failed, trying fallback {OLLAMA_FALLBACK}")
        return _ollama_generate(prompt, system, OLLAMA_FALLBACK)


def _build_context(
    query: str,
    context: Optional[str],
    beliefs: list[dict[str, Any]],
    memories: list[dict[str, Any]],
) -> str:
    belief_lines = "\n".join(
        f"  - [{b['domain']}] {b['stance']} (conf={b['confidence']:.2f}, state={b['state']})"
        for b in beliefs[:10]
    )
    memory_lines = "\n".join(
        f"  - {m['content'][:200]} (conf={m['confidence']:.2f})"
        for m in memories[:5]
    )
    ctx_section = f"\nAdditional context: {context}" if context else ""
    return (
        f"Query: {query}{ctx_section}\n\n"
        f"Active beliefs:\n{belief_lines or '  (none)'}\n\n"
        f"Recent memories:\n{memory_lines or '  (none)'}"
    )


def run_paradigm(
    query: str,
    context: Optional[str],
    model: str,
    beliefs: list[dict[str, Any]],
    memories: list[dict[str, Any]],
) -> dict[str, Any]:
    full_context = _build_context(query, context, beliefs, memories)
    try:
        raw = _call_with_fallback(full_context, PARADIGM_PROMPT, model)
        # Try to extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
        return {"complexity": 5, "relevant_beliefs": [], "route": "congress", "context_summary": raw}
    except OllamaError:
        raise
    except Exception as e:
        logger.warning(f"Paradigm JSON parse failed: {e}")
        return {"complexity": 5, "relevant_beliefs": [], "route": "congress", "context_summary": str(e)}


def run_congress(
    query: str,
    context: Optional[str],
    model: str,
    beliefs: list[dict[str, Any]],
    memories: list[dict[str, Any]],
) -> dict[str, str]:
    full_context = _build_context(query, context, beliefs, memories)
    roles = {
        "advocate": ADVOCATE_PROMPT,
        "skeptic": SKEPTIC_PROMPT,
        "synthesizer": SYNTHESIZER_PROMPT,
        "ethics": ETHICS_PROMPT,
    }
    results: dict[str, str] = {}
    for role, system_prompt in roles.items():
        try:
            results[role] = _call_with_fallback(full_context, system_prompt, model)
        except OllamaError as e:
            results[role] = f"[ERROR: {e}]"
    return results


def run_ego(
    query: str,
    congress_results: dict[str, str],
    model: str,
    beliefs: list[dict[str, Any]],
) -> str:
    belief_lines = "\n".join(
        f"  - [{b['domain']}] {b['stance']} (conf={b['confidence']:.2f})"
        for b in beliefs[:10]
    )
    prompt = (
        f"Original query: {query}\n\n"
        f"Advocate:\n{congress_results.get('advocate', '')}\n\n"
        f"Skeptic:\n{congress_results.get('skeptic', '')}\n\n"
        f"Synthesizer:\n{congress_results.get('synthesizer', '')}\n\n"
        f"Ethics:\n{congress_results.get('ethics', '')}\n\n"
        f"Core beliefs:\n{belief_lines or '  (none)'}"
    )
    try:
        return _call_with_fallback(prompt, EGO_PROMPT, model)
    except OllamaError as e:
        return f"[EGO ERROR: {e}]"


def deliberate(
    query: str,
    context: Optional[str] = None,
    model: str = OLLAMA_MODEL,
) -> dict[str, Any]:
    """
    Full Paradigm → Congress → Ego pipeline.
    Returns structured result dict for storage and tool response.
    """
    beliefs = get_beliefs()
    memories = get_memories(limit=10)

    try:
        paradigm = run_paradigm(query, context, model, beliefs, memories)
    except OllamaError as e:
        return {
            "error": str(e),
            "query": query,
            "model": model,
        }

    complexity = paradigm.get("complexity", 5)
    route = paradigm.get("route", "congress")

    if route == "direct" and complexity < 5:
        # Direct response — skip full Congress
        direct_context = _build_context(query, context, beliefs, memories)
        try:
            direct_response = _call_with_fallback(direct_context, EGO_PROMPT, model)
        except OllamaError as e:
            direct_response = f"[DIRECT RESPONSE ERROR: {e}]"
        return {
            "query": query,
            "model": model,
            "route": "direct",
            "complexity": complexity,
            "paradigm_assessment": json.dumps(paradigm),
            "advocate_position": "",
            "skeptic_position": "",
            "synthesizer_position": "",
            "ethics_review": "",
            "ego_response": direct_response,
        }

    congress_results = run_congress(query, context, model, beliefs, memories)
    ego_response = run_ego(query, congress_results, model, beliefs)

    return {
        "query": query,
        "model": model,
        "route": "congress",
        "complexity": complexity,
        "paradigm_assessment": json.dumps(paradigm),
        "advocate_position": congress_results.get("advocate", ""),
        "skeptic_position": congress_results.get("skeptic", ""),
        "synthesizer_position": congress_results.get("synthesizer", ""),
        "ethics_review": congress_results.get("ethics", ""),
        "ego_response": ego_response,
    }
