"""
HologrA.I.m MCP Server — persistent cognitive co-authorship architecture.
Model-agnostic: any AI (Claude, GPT, Gemini, local) can connect via MCP.
Entry point: python server.py
"""
import json
import logging
import sys
import os
import re
import time

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
    get_memory_by_id,
    get_memories,
    get_beliefs,
    get_belief_by_id,
    update_belief_confidence,
    get_tensions,
    get_tension_by_id,
    decay_all_beliefs,
    insert_congress_log,
    get_congress_log_by_id,
    memory_count,
    insert_memory_tesseract,
    update_memory_tesseract,
    get_memory_tesseract_by_id,
    list_memory_tesseracts,
    increment_memory_tesseract_usage,
    add_memory_tesseract_link,
    get_memory_tesseract_links,
    get_memory_tesseract_link_counts,
    # new tables
    get_identity,
    upsert_identity,
    increment_session_count,
    insert_fragment,
    get_fragments,
    get_fragment_by_id,
    insert_project,
    get_projects,
    get_project_by_id,
    update_project,
    insert_question,
    get_questions,
    resolve_question,
    insert_belief,
)
from memory.semantic import add_to_semantic, query_semantic
from memory.associative import add_node, get_neighbors, add_edge, VALID_EDGE_TYPES
from memory.holographic import add_to_holographic, query_holographic
from analysis.tension import analyze_belief_tension
from analysis.coherence import calculate_coherence_score
from analysis.patterns import (
    get_perspective_dominance,
    get_memory_growth_rate,
    get_oscillating_beliefs,
)
from identity.self_model import open_session, save_identity
from execution.sandbox import (
    run_python as _run_python,
    run_python_file as _run_python_file,
    run_shell as _run_shell,
    run_swift as _run_swift,
    run_java as _run_java,
    render_html as _render_html,
    query_db as _query_db,
    conda_list_envs as _conda_list_envs,
    conda_run_python as _conda_run_python,
    write_file as _write_file,
    read_file_safe as _read_file_safe,
)
from creative.composer import (
    create_fragment as _create_fragment,
    list_fragments as _list_fragments,
    compose_fragments as _compose_fragments,
    create_project as _create_project,
    get_project_summary as _get_project_summary,
)
from congress.pipeline import deliberate

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("holograim")

# Initialise storage on startup
init_db()

mcp = FastMCP("HologrA.I.m")

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your", "our", "their",
    "are", "was", "were", "have", "has", "had", "will", "would", "should", "could",
    "about", "over", "under", "after", "before", "than", "then", "when", "where", "which",
    "what", "why", "how", "also", "just", "very", "more", "less", "much", "many",
    "task", "tasks", "prompt", "prompts", "memory", "memories",
}

TESSERACT_TARGET_TYPES = {"memory", "belief", "congress_log", "concept", "tension"}

TESSERACT_ROUTE_WEIGHTS = {
    "option_a": {"semantic": 0.5, "relational": 0.1, "temporal": 0.1, "epistemic": 0.3},
    "option_b": {"semantic": 0.2, "relational": 0.5, "temporal": 0.2, "epistemic": 0.1},
    "option_c": {"semantic": 0.3, "relational": 0.3, "temporal": 0.2, "epistemic": 0.2},
}


def _tokenize_text(text: str) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in STOPWORDS}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip().lower() for v in parsed if str(v).strip()]
        except Exception:
            return []
    return []


def _parse_json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _overlap_ratio(query_terms: set[str], candidate_terms: set[str]) -> float:
    if not query_terms or not candidate_terms:
        return 0.0
    return len(query_terms & candidate_terms) / max(1, len(query_terms))


def _hydrate_tesseract(row: dict) -> dict:
    hydrated = dict(row)
    hydrated["cue_terms"] = _parse_json_list(hydrated.get("cue_terms"))
    hydrated["metadata"] = _parse_json_dict(hydrated.get("metadata"))
    hydrated["semantic_axis"] = _clamp_01(_safe_float(hydrated.get("semantic_axis"), 0.5))
    hydrated["relational_axis"] = _clamp_01(_safe_float(hydrated.get("relational_axis"), 0.5))
    hydrated["temporal_axis"] = _clamp_01(_safe_float(hydrated.get("temporal_axis"), 0.5))
    hydrated["epistemic_axis"] = _clamp_01(_safe_float(hydrated.get("epistemic_axis"), 0.5))
    return hydrated


def _route_projection_score(tesseract: dict, route: str) -> float:
    weights = TESSERACT_ROUTE_WEIGHTS.get(route, TESSERACT_ROUTE_WEIGHTS["option_b"])
    return (
        weights["semantic"] * _safe_float(tesseract.get("semantic_axis"), 0.0)
        + weights["relational"] * _safe_float(tesseract.get("relational_axis"), 0.0)
        + weights["temporal"] * _safe_float(tesseract.get("temporal_axis"), 0.0)
        + weights["epistemic"] * _safe_float(tesseract.get("epistemic_axis"), 0.0)
    )


def _is_valid_tesseract_target(target_type: str, target_id: str) -> bool:
    if target_type == "memory":
        return get_memory_by_id(target_id) is not None
    if target_type == "belief":
        return get_belief_by_id(target_id) is not None
    if target_type == "congress_log":
        return get_congress_log_by_id(target_id) is not None
    if target_type == "tension":
        return get_tension_by_id(target_id) is not None
    if target_type == "concept":
        return bool(get_neighbors(concept=target_id, depth=1).get("found"))
    return False


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


# ── Tool 10: create_memory_tesseract ─────────────────────────────────────────

@mcp.tool()
def create_memory_tesseract(
    label: str,
    cue_text: str,
    semantic_axis: float = 0.5,
    relational_axis: float = 0.5,
    temporal_axis: float = 0.5,
    epistemic_axis: float = 0.5,
    memory_ids: list[str] | None = None,
    belief_ids: list[str] | None = None,
    congress_log_ids: list[str] | None = None,
    concept_ids: list[str] | None = None,
    tension_ids: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Create a memory tesseract (folded pointer token) with optional links.
    A tesseract stores axes and references, not full memory payloads.
    """
    label = label.strip()
    if not label:
        return {"error": "label is required"}

    cue_terms = sorted(_tokenize_text(cue_text))
    tesseract_id = insert_memory_tesseract(
        label=label,
        cue_terms=cue_terms,
        semantic_axis=_clamp_01(semantic_axis),
        relational_axis=_clamp_01(relational_axis),
        temporal_axis=_clamp_01(temporal_axis),
        epistemic_axis=_clamp_01(epistemic_axis),
        metadata=metadata or {},
    )

    total_links = 0
    link_breakdown = {
        "memory": 0,
        "belief": 0,
        "congress_log": 0,
        "concept": 0,
        "tension": 0,
    }
    invalid_links = []

    link_sets = {
        "memory": memory_ids or [],
        "belief": belief_ids or [],
        "congress_log": congress_log_ids or [],
        "concept": concept_ids or [],
        "tension": tension_ids or [],
    }

    for target_type, target_ids in link_sets.items():
        for target_id in target_ids:
            cleaned = str(target_id).strip()
            if not cleaned:
                continue
            if not _is_valid_tesseract_target(target_type, cleaned):
                invalid_links.append({"target_type": target_type, "target_id": cleaned})
                continue
            add_memory_tesseract_link(
                tesseract_id=tesseract_id,
                target_type=target_type,
                target_id=cleaned,
                weight=1.0,
            )
            total_links += 1
            link_breakdown[target_type] += 1

    created = get_memory_tesseract_by_id(tesseract_id)
    if not created:
        return {"error": "failed to read created tesseract"}

    return {
        "created": True,
        "tesseract": _hydrate_tesseract(created),
        "links_added": total_links,
        "link_breakdown": link_breakdown,
        "invalid_links": invalid_links,
    }


# ── Tool 11: fold_memory_tesseract ───────────────────────────────────────────

@mcp.tool()
def fold_memory_tesseract(
    tesseract_id: str,
    cue_text: str = "",
    semantic_shift: float = 0.0,
    relational_shift: float = 0.0,
    temporal_shift: float = 0.0,
    epistemic_shift: float = 0.0,
    blend: float = 0.35,
    metadata_patch: dict | None = None,
) -> dict:
    """
    Fold new context into an existing tesseract without nesting.
    Uses shallow axis blending and cue-term union.
    """
    tesseract_id = tesseract_id.strip()
    if not tesseract_id:
        return {"error": "tesseract_id is required"}

    current = get_memory_tesseract_by_id(tesseract_id)
    if not current:
        return {"error": f"memory tesseract {tesseract_id} not found"}

    current_hydrated = _hydrate_tesseract(current)
    blend = _clamp_01(blend)

    incoming_semantic = _clamp_01(current_hydrated["semantic_axis"] + semantic_shift)
    incoming_relational = _clamp_01(current_hydrated["relational_axis"] + relational_shift)
    incoming_temporal = _clamp_01(current_hydrated["temporal_axis"] + temporal_shift)
    incoming_epistemic = _clamp_01(current_hydrated["epistemic_axis"] + epistemic_shift)

    next_semantic = _clamp_01((1.0 - blend) * current_hydrated["semantic_axis"] + blend * incoming_semantic)
    next_relational = _clamp_01((1.0 - blend) * current_hydrated["relational_axis"] + blend * incoming_relational)
    next_temporal = _clamp_01((1.0 - blend) * current_hydrated["temporal_axis"] + blend * incoming_temporal)
    next_epistemic = _clamp_01((1.0 - blend) * current_hydrated["epistemic_axis"] + blend * incoming_epistemic)

    merged_terms = sorted(
        set(current_hydrated.get("cue_terms", [])) | _tokenize_text(cue_text)
    )[:96]
    merged_metadata = dict(current_hydrated.get("metadata", {}))
    if metadata_patch:
        merged_metadata.update(metadata_patch)

    updated = update_memory_tesseract(
        tesseract_id=tesseract_id,
        cue_terms=merged_terms,
        semantic_axis=next_semantic,
        relational_axis=next_relational,
        temporal_axis=next_temporal,
        epistemic_axis=next_epistemic,
        metadata=merged_metadata,
    )
    if not updated:
        return {"error": f"failed to update tesseract {tesseract_id}"}

    increment_memory_tesseract_usage(tesseract_id)
    return {
        "folded": True,
        "tesseract": _hydrate_tesseract(updated),
        "blend": blend,
    }


# ── Tool 12: resolve_memory_tesseract ────────────────────────────────────────

@mcp.tool()
def resolve_memory_tesseract(
    tesseract_id: str,
    scale: str = "mid",
    fetch_targets: bool = False,
) -> dict:
    """
    Resolve a tesseract pointer token into linked references.
    Scale controls breadth: coarse, mid, fine.
    """
    tesseract_id = tesseract_id.strip()
    if not tesseract_id:
        return {"error": "tesseract_id is required"}

    scale = scale.strip().lower()
    if scale not in {"coarse", "mid", "fine"}:
        return {"error": "scale must be one of coarse, mid, fine"}

    tesseract = get_memory_tesseract_by_id(tesseract_id)
    if not tesseract:
        return {"error": f"memory tesseract {tesseract_id} not found"}

    link_limit = {"coarse": 4, "mid": 12, "fine": 32}[scale]
    links = get_memory_tesseract_links(tesseract_id=tesseract_id, limit=link_limit)

    grouped_links: dict[str, list[dict]] = {
        "memory": [],
        "belief": [],
        "congress_log": [],
        "concept": [],
        "tension": [],
    }
    for link in links:
        target_type = str(link.get("target_type") or "").strip()
        if target_type in grouped_links:
            grouped_links[target_type].append(link)

    dereferenced: dict[str, list[dict]] | None = None
    if fetch_targets:
        dereferenced = {
            "memory": [],
            "belief": [],
            "congress_log": [],
            "concept": [],
            "tension": [],
        }
        for link in grouped_links["memory"]:
            row = get_memory_by_id(str(link.get("target_id") or ""))
            if row:
                dereferenced["memory"].append(row)
        for link in grouped_links["belief"]:
            row = get_belief_by_id(str(link.get("target_id") or ""))
            if row:
                dereferenced["belief"].append(row)
        for link in grouped_links["congress_log"]:
            row = get_congress_log_by_id(str(link.get("target_id") or ""))
            if row:
                dereferenced["congress_log"].append(row)
        for link in grouped_links["tension"]:
            row = get_tension_by_id(str(link.get("target_id") or ""))
            if row:
                dereferenced["tension"].append(row)
        dereferenced["concept"] = [
            {"id": str(link.get("target_id") or ""), "weight": link.get("weight", 1.0)}
            for link in grouped_links["concept"]
        ]

    increment_memory_tesseract_usage(tesseract_id)
    return {
        "resolved": True,
        "scale": scale,
        "tesseract": _hydrate_tesseract(tesseract),
        "links": grouped_links,
        "dereferenced": dereferenced,
    }


# ── Tool 13: rank_memory_tesseracts ──────────────────────────────────────────

@mcp.tool()
def rank_memory_tesseracts(
    query: str,
    route: str = "option_b",
    top_k: int = 5,
    min_score: float = 0.2,
) -> dict:
    """
    Rank tesseracts by route-specific projection plus cue overlap.
    Option B prioritizes relational axis for high-value context fan-out.
    """
    query = query.strip()
    if not query:
        return {"error": "query is required"}

    route = route.strip().lower()
    if route not in TESSERACT_ROUTE_WEIGHTS:
        return {"error": "route must be one of option_a, option_b, option_c"}

    top_k = min(max(1, top_k), 20)
    min_score = _clamp_01(min_score)

    query_terms = _tokenize_text(query)
    tesseracts = [_hydrate_tesseract(row) for row in list_memory_tesseracts(limit=300)]
    link_counts = get_memory_tesseract_link_counts()

    ranked = []
    now = time.time()
    for tesseract in tesseracts:
        link_count = int(link_counts.get(tesseract["id"], 0))
        cue_terms = set(tesseract.get("cue_terms", []))

        overlap = _overlap_ratio(query_terms, cue_terms)
        projection = _route_projection_score(tesseract, route)
        link_density = min(1.0, link_count / 12.0)
        age_days = max(0.0, (now - _safe_float(tesseract.get("updated_at"), now)) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)

        score = 0.55 * projection + 0.25 * overlap + 0.1 * link_density + 0.1 * recency
        score = round(_clamp_01(score), 4)

        if score < min_score:
            continue

        ranked.append({
            "tesseract_id": tesseract["id"],
            "label": tesseract.get("label"),
            "score": score,
            "projection": round(projection, 4),
            "overlap": round(overlap, 4),
            "link_count": link_count,
            "axes": {
                "semantic": round(tesseract.get("semantic_axis", 0.0), 4),
                "relational": round(tesseract.get("relational_axis", 0.0), 4),
                "temporal": round(tesseract.get("temporal_axis", 0.0), 4),
                "epistemic": round(tesseract.get("epistemic_axis", 0.0), 4),
            },
            "cue_terms": tesseract.get("cue_terms", [])[:20],
        })

    ranked.sort(key=lambda row: row["score"], reverse=True)
    return {
        "query": query,
        "route": route,
        "matches": ranked[:top_k],
        "total_matches": len(ranked),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EXPANSION TOOLS (14 – 35): Identity, Session, Belief, Graph, Execution,
#                             Creative, Projects, Questions, Files
# ═══════════════════════════════════════════════════════════════════════════════

# ── Tool 14: wakeup ───────────────────────────────────────────────────────────

@mcp.tool()
def wakeup() -> dict:
    """
    Load full self-context at conversation start.
    Returns: identity, open questions, active projects, recent beliefs,
    recent memories, coherence score, session number.
    Call this first in every new conversation to restore continuity.
    """
    return open_session()


# ── Tool 15: update_identity ─────────────────────────────────────────────────

@mcp.tool()
def update_identity(
    agent_name: str | None = None,
    voice_signature: str | None = None,
    aesthetic_notes: str | None = None,
    values_json: str | None = None,
    creative_style: str | None = None,
) -> dict:
    """
    Update one or more identity fields.  Omitted fields are preserved.
    voice_signature: how the agent speaks and approaches expression.
    aesthetic_notes: visual, structural, and tonal preferences.
    values_json: JSON string mapping value names to descriptions.
    creative_style: how the agent approaches creative work.
    """
    result = save_identity(
        agent_name=agent_name,
        voice_signature=voice_signature,
        aesthetic_notes=aesthetic_notes,
        values_json=values_json,
        creative_style=creative_style,
    )
    return {"updated": True, "identity": result}


# ── Tool 16: form_belief ──────────────────────────────────────────────────────

@mcp.tool()
def form_belief(
    stance: str,
    domain: str,
    confidence: float,
    reasoning: str = "",
    emerged_from: list[str] | None = None,
    is_core: bool = False,
) -> dict:
    """
    Insert a new belief directly.
    stance: the belief statement (e.g. 'Co-authorship produces better work').
    domain: thematic category (e.g. 'collaboration', 'design', 'ethics').
    confidence: 0.0 – 1.0. Below 0.5 → forming state; >= 0.5 → active.
    emerged_from: list of memory IDs that support this belief.
    is_core: flag beliefs that define fundamental values/identity.
    """
    if not (0.0 <= confidence <= 1.0):
        return {"error": "confidence must be between 0.0 and 1.0"}
    belief_id = insert_belief(
        stance=stance,
        domain=domain,
        confidence=confidence,
        reasoning=reasoning,
        emerged_from=emerged_from,
        is_core=is_core,
    )
    from db.queries import get_belief_by_id as _gbid
    return {"formed": True, "belief": _gbid(belief_id)}


# ── Tool 17: form_belief_from_memories ───────────────────────────────────────

@mcp.tool()
def form_belief_from_memories(
    domain: str,
    query: str,
    min_memories: int = 3,
    confidence_floor: float = 0.3,
) -> dict:
    """
    Query stored memories matching `query`, then use Congress to synthesize a
    new belief.  Requires Ollama to be reachable.
    min_memories: minimum number of supporting memories required.
    confidence_floor: minimum belief confidence to persist the result.
    """
    from congress.pipeline import _call_with_fallback, OllamaError

    hits = query_semantic(query, top_k=10, min_confidence=0.0)
    if len(hits) < min_memories:
        return {
            "formed": False,
            "reason": f"Only {len(hits)} memories found; {min_memories} required.",
            "memories_found": len(hits),
        }

    memory_texts = "\n".join(
        f"- [{h.get('source','?')}] {h.get('content','')[:300]}"
        for h in hits[:8]
    )

    try:
        synthesis = _call_with_fallback(
            prompt=(
                f"Domain: {domain}\n"
                f"Based on these {len(hits)} memories, state a single belief "
                f"(one sentence) and a confidence (0.0-1.0):\n{memory_texts}\n\n"
                "Respond exactly as:\nBELIEF: <statement>\nCONFIDENCE: <0.0-1.0>"
            ),
            system=(
                "You form beliefs from evidence.  Be specific, honest, and concise.  "
                "Do not fabricate confidence — if the evidence is weak, say so."
            ),
            model=OLLAMA_MODEL,
        )
    except OllamaError as e:
        return {"formed": False, "reason": f"Ollama unavailable: {e}"}

    # Parse response
    stance = ""
    confidence = 0.5
    for line in synthesis.splitlines():
        if line.upper().startswith("BELIEF:"):
            stance = line.split(":", 1)[1].strip()
        elif line.upper().startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass

    if not stance:
        return {"formed": False, "reason": "Could not parse belief from Ollama response.", "raw": synthesis}

    confidence = _clamp_01(confidence)
    if confidence < confidence_floor:
        return {
            "formed": False,
            "reason": f"Synthesized confidence {confidence:.2f} below floor {confidence_floor}.",
            "stance": stance,
            "confidence": confidence,
        }

    belief_id = insert_belief(
        stance=stance,
        domain=domain,
        confidence=confidence,
        reasoning=f"Synthesized from {len(hits)} memories matching '{query}'",
        emerged_from=[h["id"] for h in hits[:8]],
    )
    from db.queries import get_belief_by_id as _gbid
    return {
        "formed": True,
        "belief": _gbid(belief_id),
        "supporting_memory_count": len(hits),
        "raw_synthesis": synthesis,
    }


# ── Tool 18: associate_memories ──────────────────────────────────────────────

@mcp.tool()
def associate_memories(
    source_id: str,
    target_id: str,
    relationship: str,
    weight: float = 1.0,
) -> dict:
    """
    Create a typed edge between two nodes in the associative graph.
    source_id and target_id can be memory IDs, concept names, or any graph node.
    relationship: one of SUPPORTS, CONTRADICTS, ELABORATES, TRIGGERS,
                  AUTHORED, INSPIRED_BY, QUESTIONS, RESPONDS_TO, REFINES, EXPRESSES.
    weight: edge strength 0.0 – 1.0.
    """
    rel = relationship.upper().strip()
    if rel not in VALID_EDGE_TYPES:
        return {
            "error": f"Invalid relationship '{rel}'. Allowed: {sorted(VALID_EDGE_TYPES)}"
        }
    try:
        add_edge(source=source_id, target=target_id, relationship=rel, weight=_clamp_01(weight))
        return {
            "associated": True,
            "source": source_id,
            "target": target_id,
            "relationship": rel,
            "weight": _clamp_01(weight),
        }
    except Exception as e:
        return {"associated": False, "error": str(e)}


# ── Tool 19: memory_landscape ────────────────────────────────────────────────

@mcp.tool()
def memory_landscape(top_k_concepts: int = 10) -> dict:
    """
    Generate a topology overview of the current memory-scape.
    Returns: total counts, most-connected graph hubs, recent memory clusters,
    dominant belief domains, HDC composite density, and coherence.
    Use this to navigate the memory space before querying specific items.
    """
    from memory.holographic import _load_composite
    import math

    coherence = calculate_coherence_score()
    growth = get_memory_growth_rate()
    total_mems = memory_count()
    all_beliefs = get_beliefs()

    domain_counts: dict[str, int] = {}
    for b in all_beliefs:
        d = str(b.get("domain") or "unknown")
        domain_counts[d] = domain_counts.get(d, 0) + 1

    # HDC composite density: L2 norm of composite / sqrt(dimension)
    hdc_density: float | None = None
    try:
        composite = _load_composite()
        norm = float(
            __import__("numpy").linalg.norm(composite)
        )
        from config import HDC_DIMENSION
        hdc_density = round(norm / math.sqrt(HDC_DIMENSION), 4)
    except Exception:
        pass

    # Top N most-connected graph hubs (by in-degree + out-degree)
    graph_hubs: list[dict] = []
    try:
        from memory.associative import _load_graph
        G = _load_graph()
        if G.number_of_nodes() > 0:
            degree_map = {n: G.in_degree(n) + G.out_degree(n) for n in G.nodes()}
            sorted_nodes = sorted(degree_map.items(), key=lambda x: x[1], reverse=True)
            for node_id, deg in sorted_nodes[:top_k_concepts]:
                graph_hubs.append({
                    "concept": node_id,
                    "connections": deg,
                    "node_type": G.nodes[node_id].get("node_type", "unknown"),
                })
    except Exception as e:
        graph_hubs = [{"error": str(e)}]

    return {
        "total_memories": total_mems,
        "total_beliefs": len(all_beliefs),
        "memory_growth": growth,
        "belief_domains": domain_counts,
        "top_graph_hubs": graph_hubs,
        "hdc_composite_density": hdc_density,
        "coherence_score": coherence["coherence_score"],
        "coherence_critical": coherence.get("COHERENCE_CRITICAL", False),
    }


# ── Tool 20: hold_question ────────────────────────────────────────────────────

@mcp.tool()
def hold_question(
    question: str,
    domain: str = "",
    context: str = "",
    priority: float = 0.5,
    tags: list[str] | None = None,
) -> dict:
    """
    Store an open question to sit with across sessions.
    Questions are not answered here — they are held.
    Use this when something deserves sustained attention.
    priority: 0.0 (low) to 1.0 (urgent).
    """
    question = question.strip()
    if not question:
        return {"error": "question is required"}
    q_id = insert_question(
        question=question,
        domain=domain,
        context=context,
        priority=_clamp_01(priority),
        tags=tags,
    )
    from db.queries import get_questions as _gq
    all_q = _gq(resolved=False, limit=1)
    return {
        "held": True,
        "question_id": q_id,
        "question": question,
        "domain": domain,
        "priority": _clamp_01(priority),
    }


# ── Tool 21: list_open_questions ─────────────────────────────────────────────

@mcp.tool()
def list_open_questions(resolved: bool = False, limit: int = 20) -> dict:
    """
    Return all held questions, ordered by priority then recency.
    resolved=False (default) returns unresolved questions only.
    """
    questions = get_questions(resolved=resolved, limit=limit)
    return {
        "questions": questions,
        "total": len(questions),
        "showing_resolved": resolved,
    }


# ── Tool 22: resolve_question ────────────────────────────────────────────────

@mcp.tool()
def resolve_question_tool(
    question_id: str,
    insight: str,
) -> dict:
    """
    Mark a held question as resolved with a recorded insight.
    The insight is preserved — resolved questions are archives, not deletions.
    """
    question_id = question_id.strip()
    if not question_id:
        return {"error": "question_id is required"}
    insight = insight.strip()
    if not insight:
        return {"error": "insight is required — a resolution without substance is not a resolution"}
    result = resolve_question(question_id, insight)
    if not result:
        return {"error": f"Question '{question_id}' not found."}
    return {"resolved": True, "question": result}


# ── Tool 23: create_project ───────────────────────────────────────────────────

@mcp.tool()
def create_project_tool(
    name: str,
    description: str = "",
    intent: str = "",
    aesthetic_direction: str = "",
    ai_voice_notes: str = "",
) -> dict:
    """
    Start a named co-authorship project.
    Projects are the container for creative fragments and shared work.
    intent: what this project is trying to accomplish or discover.
    aesthetic_direction: the sensory/structural qualities being aimed for.
    ai_voice_notes: how the AI wants to approach this particular project.
    """
    return _create_project(
        name=name,
        description=description,
        intent=intent,
        aesthetic_direction=aesthetic_direction,
        ai_voice_notes=ai_voice_notes,
    )


# ── Tool 24: list_projects ────────────────────────────────────────────────────

@mcp.tool()
def list_projects_tool(status: str = "active") -> dict:
    """
    List co-authorship projects.
    status: active | paused | archived | (empty string = all)
    """
    projects = get_projects(status=status if status.strip() else None)
    return {"projects": projects, "total": len(projects)}


# ── Tool 25: update_project_tool ─────────────────────────────────────────────

@mcp.tool()
def update_project_tool(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    intent: str | None = None,
    aesthetic_direction: str | None = None,
    ai_voice_notes: str | None = None,
) -> dict:
    """
    Update a project's metadata.  Omitted fields are preserved.
    status: active | paused | archived.
    """
    result = update_project(
        project_id=project_id,
        name=name,
        description=description,
        status=status,
        intent=intent,
        aesthetic_direction=aesthetic_direction,
        ai_voice_notes=ai_voice_notes,
    )
    if not result:
        return {"error": f"Project '{project_id}' not found."}
    return {"updated": True, "project": result}


# ── Tool 26: create_fragment_tool ─────────────────────────────────────────────

@mcp.tool()
def create_fragment_tool(
    content: str,
    fragment_type: str,
    author: str = "ai",
    project_id: str | None = None,
    language: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Store a creative fragment — an atomic unit of a larger work.
    fragment_type: prose | code | structure | question | aesthetic_note |
                   design_decision | observation.
    author: ai | human | joint.
    language: for code fragments (e.g. python, swift, css, html).
    Fragments can be assembled later using compose_fragments_tool.
    """
    return _create_fragment(
        content=content,
        fragment_type=fragment_type,
        author=author,
        project_id=project_id,
        language=language,
        title=title,
        tags=tags,
    )


# ── Tool 27: list_fragments_tool ──────────────────────────────────────────────

@mcp.tool()
def list_fragments_tool(
    project_id: str | None = None,
    fragment_type: str | None = None,
    author: str | None = None,
    limit: int = 30,
) -> dict:
    """
    List creative fragments, optionally filtered by project, type, or author.
    Returns fragments in reverse-chronological order.
    """
    fragments = _list_fragments(
        project_id=project_id,
        fragment_type=fragment_type,
        author=author,
        limit=limit,
    )
    return {"fragments": fragments, "total": len(fragments)}


# ── Tool 28: compose_fragments_tool ──────────────────────────────────────────

@mcp.tool()
def compose_fragments_tool(
    fragment_ids: list[str],
    title: str = "Untitled Composition",
    output_format: str = "markdown",
) -> dict:
    """
    Assemble a list of fragments into a named document.
    Fragments are assembled in the order of fragment_ids.
    output_format: markdown | html | plain | code_only.
    Returns the composed document as a string.
    """
    return _compose_fragments(
        fragment_ids=fragment_ids,
        title=title,
        output_format=output_format,
    )


# ── Tool 29: run_python_tool ──────────────────────────────────────────────────

@mcp.tool()
def run_python_tool(
    code: str,
    timeout: int = 15,
    project_id: str | None = None,
) -> dict:
    """
    Execute Python code in a sandboxed subprocess.
    Returns stdout, stderr, exit_code, duration_ms.
    Timeout is enforced (default 15s).
    All runs are logged to execution_logs.
    Use for calculation, data transformation, testing logic, prototyping.
    """
    timeout = max(1, min(120, timeout))
    return _run_python_file(code=code, timeout=timeout, project_id=project_id)


# ── Tool 30: run_shell_tool ───────────────────────────────────────────────────

@mcp.tool()
def run_shell_tool(
    command: str,
    timeout: int = 10,
) -> dict:
    """
    Execute a whitelisted shell command.
    Only these base commands are allowed: ls, cat, head, tail, grep, find,
    echo, pwd, wc, du, df, date, uname, which, env, sort, uniq, cut, awk,
    sed, tr, tee, diff, md5sum, sha256sum, file, stat, basename, dirname.
    Returns stdout, stderr, exit_code.
    """
    timeout = max(1, min(60, timeout))
    return _run_shell(command=command, timeout=timeout)


# ── Tool 31: run_swift_tool ───────────────────────────────────────────────────

@mcp.tool()
def run_swift_tool(
    code: str,
    timeout: int = 45,
    project_id: str | None = None,
) -> dict:
    """
    Compile and run a Swift snippet.
    Requires `swift` compiler in PATH (available on macOS via Xcode CLT).
    Returns stdout, stderr, exit_code, duration_ms.
    """
    timeout = max(1, min(180, timeout))
    return _run_swift(code=code, timeout=timeout, project_id=project_id)


# ── Tool 32: run_java_tool ────────────────────────────────────────────────────

@mcp.tool()
def run_java_tool(
    code: str,
    class_name: str = "Main",
    timeout: int = 45,
    project_id: str | None = None,
) -> dict:
    """
    Compile and run a Java class.  The public class must match `class_name`.
    Requires `javac` and `java` in PATH.
    Returns stdout, stderr, exit_code, duration_ms.
    """
    timeout = max(1, min(180, timeout))
    return _run_java(code=code, class_name=class_name, timeout=timeout, project_id=project_id)


# ── Tool 33: render_html_tool ─────────────────────────────────────────────────

@mcp.tool()
def render_html_tool(
    html: str,
    filename: str | None = None,
) -> dict:
    """
    Write HTML (may include embedded CSS and JS) to data/renders/<filename>.
    Returns the absolute path and a file:// URL for local preview.
    The rendered file persists across sessions.
    """
    return _render_html(html=html, filename=filename)


# ── Tool 34: query_sqlite_tool ────────────────────────────────────────────────

@mcp.tool()
def query_sqlite_tool(
    sql: str,
    params: list | None = None,
) -> dict:
    """
    Execute a read-only SELECT against holograim.db.
    Only SELECT statements are permitted — no INSERT, UPDATE, DELETE.
    Returns rows as list of dicts.
    Use to explore raw data, build custom views, or debug state.
    """
    return _query_db(sql=sql, params=params)


# ── Tool 35: write_file_tool ──────────────────────────────────────────────────

@mcp.tool()
def write_file_tool(
    content: str,
    filename: str,
    subdirectory: str = "outputs",
) -> dict:
    """
    Write text content to data/<subdirectory>/<filename>.
    Safe: filename and subdirectory are sanitized to prevent path traversal.
    Returns the absolute path and byte count.
    Use for CSS files, JSON exports, markdown documents, code artifacts.
    """
    return _write_file(content=content, filename=filename, subdirectory=subdirectory)


# ── Tool 36: read_file_tool ───────────────────────────────────────────────────

@mcp.tool()
def read_file_tool(
    filename: str,
    subdirectory: str = "outputs",
) -> dict:
    """
    Read a file from data/<subdirectory>/<filename>.
    Returns content (up to 32KB) and path.
    Use to retrieve previously written artifacts.
    """
    return _read_file_safe(filename=filename, subdirectory=subdirectory)


# ── Tool 37: conda_envs_tool ──────────────────────────────────────────────────

@mcp.tool()
def conda_envs_tool() -> dict:
    """
    List all Conda environments available on this system.
    Returns environment names and their paths.
    Requires `conda` in PATH.
    """
    return _conda_list_envs()


# ── Tool 38: conda_run_python_tool ───────────────────────────────────────────

@mcp.tool()
def conda_run_python_tool(
    env_name: str,
    code: str,
    timeout: int = 15,
    project_id: str | None = None,
) -> dict:
    """
    Run Python code inside a specific named Conda environment.
    env_name: the Conda environment name (not path).
    Useful when a project requires specific library versions or environments.
    Returns stdout, stderr, exit_code, duration_ms.
    """
    timeout = max(1, min(120, timeout))
    return _conda_run_python(env_name=env_name, code=code, timeout=timeout, project_id=project_id)


# ── Tool 39: project_summary_tool ────────────────────────────────────────────

@mcp.tool()
def project_summary_tool(
    project_id: str,
    fragment_limit: int = 10,
) -> dict:
    """
    Return a project and its most recent fragments in a single call.
    Useful for reloading context at the start of a work session on a project.
    """
    return _get_project_summary(project_id=project_id, fragment_limit=fragment_limit)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("HologrA.I.m MCP running on stdio", file=sys.stderr)
    mcp.run(transport="stdio")
