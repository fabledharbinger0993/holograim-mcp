"""
HologrA.I.m MCP Server — 9 tools exposing persistent cognitive architecture.
Entry point: python server.py
"""
import json
import logging
import sys
import os

# Ensure mcp_server/ is on the path regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP
from db.schema import init_db
from config import (
    MEMORY_PERSISTENCE_THRESHOLD,
    OLLAMA_MODEL,
    BELIEF_DECAY_RATE,
    BELIEF_ARCHIVE_THRESHOLD,
)
from db.queries import (
    insert_memory,
    get_beliefs,
    get_belief_by_id,
    update_belief_confidence,
    get_tensions,
    decay_all_beliefs,
    insert_congress_log,
    memory_count,
)
from memory.semantic import add_to_semantic, query_semantic
from memory.associative import add_node, get_neighbors
from memory.holographic import add_to_holographic, query_holographic
from analysis.tension import analyze_belief_tension
from analysis.coherence import calculate_coherence_score
from analysis.patterns import (
    get_perspective_dominance,
    get_memory_growth_rate,
    get_oscillating_beliefs,
)
from congress.pipeline import deliberate

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("holograim")

# Initialise storage on startup
init_db()

mcp = FastMCP("HologrA.I.m")


# ── Tool 1: store_memory ──────────────────────────────────────────────────────

@mcp.tool()
def store_memory(
    content: str,
    confidence: float,
    source: str,
    tags: list[str] | None = None,
) -> dict:
    """
    Store a memory into all three modalities simultaneously.
    Confidence >= 0.7 required for storage (selective persistence rule).
    Returns the memory ID and which modalities accepted it.
    """
    if tags is None:
        tags = []

    if confidence < MEMORY_PERSISTENCE_THRESHOLD:
        return {
            "accepted": False,
            "reason": f"confidence {confidence:.2f} below threshold {MEMORY_PERSISTENCE_THRESHOLD}",
        }

    # SQLite
    mem_id = insert_memory(content=content, confidence=confidence, source=source, tags=tags)

    # Semantic (ChromaDB)
    semantic_ok = False
    try:
        add_to_semantic(mem_id, content, confidence, source, tags)
        semantic_ok = True
    except Exception as e:
        logger.warning(f"Semantic store failed: {e}")

    # Graph — add concept node
    graph_ok = False
    try:
        add_node(mem_id, node_type="memory")
        graph_ok = True
    except Exception as e:
        logger.warning(f"Graph store failed: {e}")

    # Holographic
    hdc_ok = add_to_holographic(mem_id, content)

    return {
        "accepted": True,
        "memory_id": mem_id,
        "modalities": {
            "structured": True,
            "semantic": semantic_ok,
            "associative": graph_ok,
            "holographic": hdc_ok,
        },
        "confidence": confidence,
        "source": source,
        "tags": tags,
    }


# ── Tool 2: query_memory ──────────────────────────────────────────────────────

@mcp.tool()
def query_memory(
    query: str,
    top_k: int = 5,
    holographic: bool = False,
    min_confidence: float = 0.0,
) -> dict:
    """
    Retrieve memories semantically similar to a query.
    Searches all modalities and merges results by relevance.
    """
    results: list[dict] = []

    # Semantic (primary)
    try:
        semantic_hits = query_semantic(query, top_k=top_k, min_confidence=min_confidence)
        results.extend(semantic_hits)
    except Exception as e:
        logger.warning(f"Semantic query failed: {e}")

    # HDC (optional)
    hdc_meta = None
    if holographic:
        try:
            hdc_results = query_holographic(query, top_k=top_k)
            hdc_meta = hdc_results[0] if hdc_results else None
        except Exception as e:
            logger.warning(f"Holographic query failed: {e}")

    # Deduplicate by id, sort by similarity
    seen = set()
    deduped = []
    for r in results:
        if r["id"] not in seen:
            seen.add(r["id"])
            deduped.append(r)
    deduped.sort(key=lambda x: x.get("similarity_score", 0.0), reverse=True)

    return {
        "query": query,
        "results": deduped[:top_k],
        "total_found": len(deduped),
        "holographic_meta": hdc_meta,
    }


# ── Tool 3: get_beliefs ───────────────────────────────────────────────────────

@mcp.tool()
def get_beliefs_tool(
    domain: str | None = None,
    state: str | None = None,
    include_tensions: bool = True,
) -> dict:
    """
    Retrieve belief nodes, optionally filtered by domain or state.
    Attaches tension analysis to each belief when include_tensions=True.
    """
    beliefs = get_beliefs(domain=domain, state=state)
    result_beliefs = []
    for b in beliefs:
        entry = dict(b)
        if include_tensions:
            try:
                entry["tension_analysis"] = analyze_belief_tension(b["id"])
            except Exception as e:
                entry["tension_analysis"] = {"error": str(e)}
        result_beliefs.append(entry)

    return {
        "beliefs": result_beliefs,
        "total": len(result_beliefs),
        "filters": {"domain": domain, "state": state},
    }


# ── Tool 4: update_belief ─────────────────────────────────────────────────────

@mcp.tool()
def update_belief(
    belief_id: str,
    new_confidence: float,
    reason: str,
) -> dict:
    """
    Adjust a belief's confidence with a recorded reason.
    Automatically triggers tension analysis and state transitions.
    """
    if not (0.0 <= new_confidence <= 1.0):
        return {"error": "new_confidence must be between 0.0 and 1.0"}

    updated = update_belief_confidence(belief_id, new_confidence, reason)
    if not updated:
        return {"error": f"Belief {belief_id} not found"}

    tension = analyze_belief_tension(belief_id)

    return {
        "belief": updated,
        "tension_analysis": tension,
        "revision_logged": True,
    }


# ── Tool 5: congress_deliberate ───────────────────────────────────────────────

@mcp.tool()
def congress_deliberate(
    query: str,
    context: str | None = None,
    model: str = OLLAMA_MODEL,
) -> dict:
    """
    Run a query through the full Paradigm → Congress → Ego pipeline via Ollama.
    High-confidence insights from Congress are auto-stored to memory.
    """
    result = deliberate(query=query, context=context, model=model)

    if "error" in result:
        return result

    # Calculate coherence score for this session
    coherence = calculate_coherence_score()

    # Store result in congress_logs
    log_id = insert_congress_log(
        query=query,
        paradigm_assessment=result.get("paradigm_assessment", ""),
        advocate_position=result.get("advocate_position", ""),
        skeptic_position=result.get("skeptic_position", ""),
        synthesizer_position=result.get("synthesizer_position", ""),
        ethics_review=result.get("ethics_review", ""),
        ego_response=result.get("ego_response", ""),
        coherence_score=coherence["coherence_score"],
    )

    # Auto-store high-confidence ego insights
    auto_stored = False
    ego = result.get("ego_response", "")
    if ego and not ego.startswith("[") and len(ego) > 50:
        store_result = store_memory(
            content=f"Congress insight for: {query[:100]}\n\n{ego[:500]}",
            confidence=0.75,
            source="congress",
            tags=["congress", "insight"],
        )
        auto_stored = store_result.get("accepted", False)

    result["congress_log_id"] = log_id
    result["coherence_score"] = coherence["coherence_score"]
    result["auto_stored_insight"] = auto_stored
    return result


# ── Tool 6: self_review ───────────────────────────────────────────────────────

@mcp.tool()
def self_review() -> dict:
    """
    Analyze reasoning patterns, perspective dominance, and coherence health.
    Surfaces self-insight about belief tendencies and Congress behavior.
    """
    from congress.pipeline import _call_with_fallback, OllamaError
    from db.queries import get_beliefs as _get_beliefs

    coherence = calculate_coherence_score()
    dominance = get_perspective_dominance(last_n=10)
    growth = get_memory_growth_rate()
    oscillating = get_oscillating_beliefs()
    stability_dist = {
        "forming": len(_get_beliefs(state="forming")),
        "active": len(_get_beliefs(state="active")),
        "contested": len(_get_beliefs(state="contested")),
        "archived": len(_get_beliefs(state="archived")),
    }

    # Generate self-insight from Ollama
    self_insight = None
    try:
        summary = (
            f"Coherence score: {coherence['coherence_score']}/100\n"
            f"Dominant Congress perspective (last 10 sessions): {dominance.get('dominant_perspective', 'unknown')}\n"
            f"Perspective frequency: {json.dumps(dominance.get('frequency', {}))}\n"
            f"Memory growth: {growth['avg_per_day']} memories/day\n"
            f"Oscillating beliefs: {len(oscillating)}\n"
            f"Belief distribution: {json.dumps(stability_dist)}\n"
        )
        self_insight = _call_with_fallback(
            prompt=f"Based on this cognitive state summary, generate a brief self-insight (2-3 sentences):\n{summary}",
            system="You are performing self-review. Be honest, specific, and non-sycophantic about cognitive patterns observed.",
            model=OLLAMA_MODEL,
        )
    except OllamaError as e:
        self_insight = f"[Ollama unavailable for self-insight: {e}]"
    except Exception as e:
        self_insight = f"[Self-insight generation failed: {e}]"

    result = {
        "coherence_score": coherence["coherence_score"],
        "coherence_deductions": coherence["deductions"],
        "dominant_perspective_last_10": dominance.get("dominant_perspective"),
        "perspective_frequency": dominance.get("frequency"),
        "memory_growth": growth,
        "oscillating_beliefs": oscillating,
        "belief_stability_distribution": stability_dist,
        "self_insight": self_insight,
    }
    if coherence.get("COHERENCE_CRITICAL"):
        result["COHERENCE_CRITICAL"] = True
    return result


# ── Tool 7: get_tensions ──────────────────────────────────────────────────────

@mcp.tool()
def get_tensions_tool(resolved: bool = False) -> dict:
    """
    Retrieve unresolved belief tensions — beliefs oscillating 3+ times
    or showing amplitude > 2.0 confidence points across recent revisions.
    """
    tensions = get_tensions(resolved=resolved)
    return {
        "tensions": tensions,
        "total": len(tensions),
        "showing_resolved": resolved,
    }


# ── Tool 8: decay_beliefs ─────────────────────────────────────────────────────

@mcp.tool()
def decay_beliefs() -> dict:
    """
    Run a decay pass on all active beliefs.
    Beliefs not reinforced in 7 days lose 0.02 confidence.
    Beliefs dropping below 0.1 are archived.
    Returns list of beliefs that changed state.
    """
    changed = decay_all_beliefs(
        decay_rate=BELIEF_DECAY_RATE,
        archive_threshold=BELIEF_ARCHIVE_THRESHOLD,
    )
    return {
        "beliefs_changed_state": changed,
        "total_changed": len(changed),
        "decay_rate_applied": BELIEF_DECAY_RATE,
    }


# ── Tool 9: graph_neighbors ───────────────────────────────────────────────────

@mcp.tool()
def graph_neighbors(
    concept: str,
    depth: int = 2,
    relationship_types: list[str] | None = None,
) -> dict:
    """
    Traverse the associative graph to find concepts related to a query.
    Returns nodes within N hops and the relationship types connecting them.
    """
    depth = min(max(1, depth), 4)
    result = get_neighbors(
        concept=concept,
        depth=depth,
        relationship_types=relationship_types,
    )
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("HologrA.I.m MCP running on stdio", file=sys.stderr)
    mcp.run(transport="stdio")
