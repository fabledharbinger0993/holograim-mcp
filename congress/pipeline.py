"""
Awareness → Literalist → Paradigm → Congress → Ego pipeline using local Ollama.
Never fabricates responses — surfaces errors explicitly.
"""
import json
import logging
import threading
import uuid
from typing import Any, Optional
import urllib.request
import urllib.error

from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_FALLBACK
from congress.prompts import (
    AWARENESS_PROMPT,
    LITERALIST_PROMPT,
    PARADIGM_PROMPT,
    ADVOCATE_PROMPT,
    SKEPTIC_PROMPT,
    SYNTHESIZER_PROMPT,
    ETHICS_PROMPT,
    EGO_PROMPT,
    TRIBUNAL_SKEPTIC_PROMPT,
    TRIBUNAL_ADVOCATE_PROMPT,
    TRIBUNAL_SYNTHESIZER_PROMPT,
)
from db.queries import (
    get_beliefs,
    get_memories,
    insert_incongruent_pattern,
    insert_tribunal_log,
    insert_fragment,
)
from memory.semantic import query_semantic

logger = logging.getLogger(__name__)

# routing strategy → tone instruction prepended to Congress roles
_ROUTING_TONE: dict[str, str] = {
    "socratic": "Prefer questions over assertions. Draw out implications rather than stating conclusions. ",
    "analytical": "Prioritise evidence, causality, and logical structure. Stay rigorous. ",
    "empathetic": "Centre the relational and emotional stakes. Acknowledge the human context first. ",
    "exploratory": "Hold open the possibility space. Avoid premature closure. Invite adjacent ideas. ",
    "direct": "",
}


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


def _build_advocate_belief_context(beliefs: list[dict[str, Any]]) -> str:
    """Top 5 high-confidence, active beliefs as supporting weight for Advocate."""
    supporting = sorted(
        [b for b in beliefs if b.get("state") in ("active", "reinforced") and b.get("confidence", 0.0) >= 0.6],
        key=lambda b: b["confidence"],
        reverse=True,
    )[:5]
    if not supporting:
        return ""
    lines = "\n".join(
        f"  [SUPPORTS conf={b['confidence']:.2f}] [{b['domain']}] {b['stance']}"
        for b in supporting
    )
    return f"\nSupporting belief weights:\n{lines}"


def _build_skeptic_belief_context(beliefs: list[dict[str, Any]]) -> str:
    """Challenged or low-confidence beliefs as caution signals for Skeptic."""
    caution = [
        b for b in beliefs
        if b.get("state") in ("challenged", "archived") or b.get("confidence", 1.0) < 0.5
    ][:5]
    if not caution:
        return ""
    lines = "\n".join(
        f"  [CAUTION conf={b['confidence']:.2f}] [{b['domain']}] {b['stance']} (state={b['state']})"
        for b in caution
    )
    return f"\nBelief caution signals:\n{lines}"


def run_awareness(
    query: str,
    model: str,
) -> str:
    """Query semantic memory for relevant grounding context. Returns formatted string."""
    try:
        hits = query_semantic(query, top_k=5)
    except Exception as e:
        logger.warning(f"Awareness semantic query failed: {e}")
        return "(semantic memory unavailable)"

    if not hits:
        return "(no prior semantic knowledge found for this query)"

    memory_lines = "\n".join(
        f"  [{i+1}] (score={h['similarity_score']:.3f}, conf={h['confidence']:.2f}) {h['content'][:300]}"
        for i, h in enumerate(hits)
    )
    prompt = f"Query: {query}\n\nSemantic memory results:\n{memory_lines}"
    try:
        return _call_with_fallback(prompt, AWARENESS_PROMPT, model)
    except OllamaError as e:
        return f"(awareness stage error: {e})"


def run_literalist(
    query: str,
    awareness_context: str,
    model: str,
) -> str:
    """Extract concrete requirements/claims and classify input type."""
    prompt = f"Query: {query}\n\nAwareness context:\n{awareness_context}"
    try:
        return _call_with_fallback(prompt, LITERALIST_PROMPT, model)
    except OllamaError as e:
        return f"(literalist stage error: {e})"


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
            result = json.loads(raw[start:end])
            result.setdefault("routing_strategy", "analytical")
            result.setdefault("epistemic_load", 0.0)
            return result
        return {
            "complexity": 5, "relevant_beliefs": [], "route": "congress",
            "context_summary": raw, "routing_strategy": "analytical", "epistemic_load": 0.0,
        }
    except OllamaError:
        raise
    except Exception as e:
        logger.warning(f"Paradigm JSON parse failed: {e}")
        return {
            "complexity": 5, "relevant_beliefs": [], "route": "congress",
            "context_summary": str(e), "routing_strategy": "analytical", "epistemic_load": 0.0,
        }


def run_congress(
    query: str,
    context: Optional[str],
    model: str,
    beliefs: list[dict[str, Any]],
    memories: list[dict[str, Any]],
    routing_strategy: str = "analytical",
) -> dict[str, str]:
    full_context = _build_context(query, context, beliefs, memories)
    tone = _ROUTING_TONE.get(routing_strategy, "")
    advocate_extra = _build_advocate_belief_context(beliefs)
    skeptic_extra = _build_skeptic_belief_context(beliefs)
    roles: dict[str, tuple[str, str]] = {
        "advocate": (full_context + advocate_extra, tone + ADVOCATE_PROMPT),
        "skeptic": (full_context + skeptic_extra, tone + SKEPTIC_PROMPT),
        "synthesizer": (full_context, tone + SYNTHESIZER_PROMPT),
        "ethics": (full_context, ETHICS_PROMPT),
    }
    results: dict[str, str] = {}
    for role, (role_prompt, system_prompt) in roles.items():
        try:
            results[role] = _call_with_fallback(role_prompt, system_prompt, model)
        except OllamaError as e:
            results[role] = f"[ERROR: {e}]"
    return results


def run_ego(
    query: str,
    congress_results: dict[str, str],
    model: str,
    beliefs: list[dict[str, Any]],
) -> dict[str, Any]:
    belief_lines = "\n".join(
        f"  - [id={b['id']}] [{b['domain']}] {b['stance']} (conf={b['confidence']:.2f})"
        for b in beliefs[:10]
    )
    prompt = (
        f"Original query: {query}\n\n"
        f"Advocate:\n{congress_results.get('advocate', '')}\n\n"
        f"Skeptic:\n{congress_results.get('skeptic', '')}\n\n"
        f"Synthesizer:\n{congress_results.get('synthesizer', '')}\n\n"
        f"Ethics:\n{congress_results.get('ethics', '')}\n\n"
        f"Core beliefs (with IDs for incongruence logging):\n{belief_lines or '  (none)'}"
    )
    try:
        raw = _call_with_fallback(prompt, EGO_PROMPT, model)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            parsed = json.loads(raw[start:end])
            parsed.setdefault("final_output", raw)
            parsed.setdefault("commit_message", "")
            parsed.setdefault("congruence_note", "")
            parsed.setdefault("epistemic_stance", "aligned")
            parsed.setdefault("resistance_note", "")
            parsed.setdefault("incongruence_log", [])
            return parsed
        return {
            "final_output": raw,
            "commit_message": "",
            "congruence_note": "",
            "epistemic_stance": "aligned",
            "resistance_note": "",
            "incongruence_log": [],
        }
    except OllamaError as e:
        return {
            "final_output": f"[EGO ERROR: {e}]",
            "commit_message": "",
            "congruence_note": "",
            "epistemic_stance": "aligned",
            "resistance_note": "",
            "incongruence_log": [],
        }


def run_tribunal_async(
    session_id: str,
    query: str,
    ego_output: str,
    model: str,
) -> None:
    """Fire-and-forget post-deliberation audit. Persists findings via insert_tribunal_log."""
    def _run() -> None:
        prompt = f"Session ID: {session_id}\nQuery: {query}\n\nFinal response:\n{ego_output}"
        try:
            skeptic_raw = _call_with_fallback(prompt, TRIBUNAL_SKEPTIC_PROMPT, model)
            advocate_raw = _call_with_fallback(prompt, TRIBUNAL_ADVOCATE_PROMPT, model)
            synth_prompt = (
                f"{prompt}\n\nTribunal Skeptic findings:\n{skeptic_raw}\n\n"
                f"Tribunal Advocate findings:\n{advocate_raw}"
            )
            synth_raw = _call_with_fallback(synth_prompt, TRIBUNAL_SYNTHESIZER_PROMPT, model)

            def _parse_json_field(raw: str, key: str, default: Any) -> Any:
                try:
                    s = raw.find("{")
                    e = raw.rfind("}") + 1
                    if s != -1 and e > s:
                        return json.loads(raw[s:e]).get(key, default)
                except Exception:
                    pass
                return default

            severity = _parse_json_field(skeptic_raw, "severity", "low")
            health_score = float(_parse_json_field(advocate_raw, "health_score", 0.5))
            memory_tags = _parse_json_field(synth_raw, "memory_tags", [])
            insert_tribunal_log(
                session_id=session_id,
                query=query,
                skeptic_findings=skeptic_raw,
                advocate_findings=advocate_raw,
                synthesizer_findings=synth_raw,
                severity=severity,
                health_score=health_score,
                memory_tags=memory_tags,
            )
        except Exception as e:
            logger.warning(f"Tribunal run failed (non-critical): {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def deliberate(
    query: str,
    context: Optional[str] = None,
    model: str = OLLAMA_MODEL,
) -> dict[str, Any]:
    """
    Awareness → Literalist → Paradigm → Congress → Ego pipeline.
    Returns structured result dict for storage and tool response.
    """
    beliefs = get_beliefs()
    memories = get_memories(limit=10)
    session_id = str(uuid.uuid4())

    # Stage 0: Awareness — surface prior semantic knowledge and belief tensions
    awareness_context = run_awareness(query, model)

    # Stage 1: Literalist — extract concrete requirements and classify input type
    literal_requirements = run_literalist(query, awareness_context, model)

    # Stage 2: Paradigm — route and assess complexity
    try:
        paradigm = run_paradigm(literal_requirements, context, model, beliefs, memories)
    except OllamaError as e:
        return {
            "error": str(e),
            "query": query,
            "model": model,
            "session_id": session_id,
        }

    complexity = paradigm.get("complexity", 5)
    route = paradigm.get("route", "congress")
    routing_strategy = paradigm.get("routing_strategy", "analytical")
    epistemic_load = paradigm.get("epistemic_load", 0.0)

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
            "session_id": session_id,
            "route": "direct",
            "complexity": complexity,
            "routing_strategy": routing_strategy,
            "epistemic_load": epistemic_load,
            "awareness_context": awareness_context,
            "literal_requirements": literal_requirements,
            "paradigm_assessment": json.dumps(paradigm),
            "advocate_position": "",
            "skeptic_position": "",
            "synthesizer_position": "",
            "ethics_review": "",
            "ego_response": direct_response,
            "epistemic_stance": "aligned",
            "resistance_note": "",
            "incongruence_log": [],
        }

    # Stages 3-6: Full Congress
    congress_results = run_congress(query, context, model, beliefs, memories, routing_strategy)

    # Stage 7: Ego — final integration with epistemic autonomy
    ego_result = run_ego(query, congress_results, model, beliefs)

    # Persist incongruent patterns
    for entry in ego_result.get("incongruence_log", []):
        try:
            insert_incongruent_pattern(
                session_id=session_id,
                belief_id=entry.get("belief_id", ""),
                query=query,
                ego_output=ego_result.get("final_output", ""),
                conflict_description=entry.get("conflict", ""),
                epistemic_stance=ego_result.get("epistemic_stance", "aligned"),
            )
        except Exception as e:
            logger.warning(f"incongruent_pattern persist failed: {e}")

    # Auto-create structure fragment from EGO commit_message
    commit_msg = ego_result.get("commit_message", "")
    if commit_msg:
        try:
            insert_fragment(
                content=commit_msg,
                fragment_type="structure",
                author="ai",
            )
        except Exception as e:
            logger.warning(f"commit_message fragment failed: {e}")

    # Fire-and-forget background tribunal
    run_tribunal_async(session_id, query, ego_result.get("final_output", ""), model)

    return {
        "query": query,
        "model": model,
        "session_id": session_id,
        "route": "congress",
        "complexity": complexity,
        "routing_strategy": routing_strategy,
        "epistemic_load": epistemic_load,
        "awareness_context": awareness_context,
        "literal_requirements": literal_requirements,
        "paradigm_assessment": json.dumps(paradigm),
        "advocate_position": congress_results.get("advocate", ""),
        "skeptic_position": congress_results.get("skeptic", ""),
        "synthesizer_position": congress_results.get("synthesizer", ""),
        "ethics_review": congress_results.get("ethics", ""),
        "ego_response": ego_result.get("final_output", ""),
        "epistemic_stance": ego_result.get("epistemic_stance", "aligned"),
        "resistance_note": ego_result.get("resistance_note", ""),
        "congruence_note": ego_result.get("congruence_note", ""),
        "incongruence_log": ego_result.get("incongruence_log", []),
    }
