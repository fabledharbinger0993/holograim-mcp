import json
import time
import uuid
from typing import Any, Optional
from db.schema import get_connection

TESSERACT_TARGET_TYPES = {"memory", "belief", "congress_log", "concept", "tension"}


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ── Memories ──────────────────────────────────────────────────────────────────

def insert_memory(
    content: str,
    confidence: float,
    source: str,
    tags: list[str],
) -> str:
    mem_id = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memories (id, content, confidence, source, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mem_id, content, confidence, source, json.dumps(tags), now),
        )
        conn.commit()
    return mem_id


def get_memories(
    min_confidence: float = 0.0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE confidence >= ? ORDER BY created_at DESC LIMIT ?",
            (min_confidence, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_memory_by_id(mem_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    return dict(row) if row else None


def reinforce_memory(mem_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE memories SET reinforcement_count = reinforcement_count + 1 WHERE id = ?",
            (mem_id,),
        )
        conn.commit()


def get_recent_memories(days: int = 30, limit: int = 50) -> list[dict[str, Any]]:
    cutoff = time.time() - days * 86400
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Beliefs ───────────────────────────────────────────────────────────────────

def _confidence_to_weight(confidence: float) -> int:
    return max(1, min(10, round(confidence * 10)))


def insert_belief(
    stance: str,
    domain: str,
    confidence: float,
    reasoning: str = "",
    emerged_from: Optional[list[str]] = None,
    is_core: bool = False,
) -> str:
    belief_id = str(uuid.uuid4())
    now = time.time()
    state = "forming" if confidence < 0.5 else "active"
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO beliefs
               (id, stance, domain, confidence, weight, state, is_core,
                reasoning, emerged_from, created_at, revision_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                belief_id,
                stance,
                domain,
                confidence,
                _confidence_to_weight(confidence),
                state,
                int(is_core),
                reasoning,
                json.dumps(emerged_from or []),
                now,
            ),
        )
        conn.commit()
    return belief_id


def get_beliefs(
    domain: Optional[str] = None,
    state: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM beliefs WHERE 1=1"
    params: list[Any] = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if state:
        query += " AND state = ?"
        params.append(state)
    query += " ORDER BY confidence DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_belief_by_id(belief_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
    return dict(row) if row else None


def update_belief_confidence(
    belief_id: str,
    new_confidence: float,
    reason: str,
) -> Optional[dict[str, Any]]:
    now = time.time()
    belief = get_belief_by_id(belief_id)
    if not belief:
        return None

    old_confidence = belief["confidence"]
    new_weight = _confidence_to_weight(new_confidence)

    # State transitions
    new_state = belief["state"]
    if new_confidence < 0.1:
        new_state = "archived"
    elif new_confidence < 0.5:
        new_state = "forming"
    elif belief["state"] == "forming" and new_confidence >= 0.5:
        new_state = "active"

    rev_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """UPDATE beliefs
               SET confidence = ?, weight = ?, state = ?,
                   last_reinforced = ?, revision_count = revision_count + 1
               WHERE id = ?""",
            (new_confidence, new_weight, new_state, now, belief_id),
        )
        conn.execute(
            """INSERT INTO belief_revisions
               (id, belief_id, previous_confidence, new_confidence, reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rev_id, belief_id, old_confidence, new_confidence, reason, now),
        )
        conn.commit()

    return get_belief_by_id(belief_id)


def get_belief_revisions(belief_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM belief_revisions WHERE belief_id = ? ORDER BY timestamp ASC",
            (belief_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def decay_all_beliefs(decay_rate: float, archive_threshold: float) -> list[dict[str, Any]]:
    week_ago = time.time() - 7 * 86400
    with get_connection() as conn:
        stale = conn.execute(
            """SELECT * FROM beliefs
               WHERE state IN ('forming', 'active', 'contested')
               AND (last_reinforced IS NULL OR last_reinforced < ?)""",
            (week_ago,),
        ).fetchall()

    changed = []
    for row in stale:
        b = dict(row)
        new_conf = max(0.0, b["confidence"] - decay_rate)
        updated = update_belief_confidence(
            b["id"],
            new_conf,
            "weekly_decay",
        )
        if updated and updated["state"] != b["state"]:
            changed.append(updated)
    return changed


# ── Congress Logs ─────────────────────────────────────────────────────────────

def insert_congress_log(
    query: str,
    paradigm_assessment: str,
    advocate_position: str,
    skeptic_position: str,
    synthesizer_position: str,
    ethics_review: str,
    ego_response: str,
    coherence_score: float,
) -> str:
    log_id = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO congress_logs
               (id, query, paradigm_assessment, advocate_position,
                skeptic_position, synthesizer_position, ethics_review,
                ego_response, coherence_score, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                query,
                paradigm_assessment,
                advocate_position,
                skeptic_position,
                synthesizer_position,
                ethics_review,
                ego_response,
                coherence_score,
                now,
            ),
        )
        conn.commit()
    return log_id


def get_recent_congress_logs(limit: int = 10) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM congress_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_congress_log_by_id(log_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM congress_logs WHERE id = ?", (log_id,)).fetchone()
    return dict(row) if row else None


# ── Memory Tesseracts ────────────────────────────────────────────────────────

def insert_memory_tesseract(
    label: str,
    cue_terms: list[str],
    semantic_axis: float,
    relational_axis: float,
    temporal_axis: float,
    epistemic_axis: float,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    tesseract_id = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memory_tesseracts
               (id, label, cue_terms, semantic_axis, relational_axis,
                temporal_axis, epistemic_axis, metadata, usage_count,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                tesseract_id,
                label,
                json.dumps(cue_terms),
                _clamp_01(semantic_axis),
                _clamp_01(relational_axis),
                _clamp_01(temporal_axis),
                _clamp_01(epistemic_axis),
                json.dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
    return tesseract_id


def update_memory_tesseract(
    tesseract_id: str,
    cue_terms: list[str],
    semantic_axis: float,
    relational_axis: float,
    temporal_axis: float,
    epistemic_axis: float,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    now = time.time()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM memory_tesseracts WHERE id = ?", (tesseract_id,)).fetchone()
        if not row:
            return None

        conn.execute(
            """UPDATE memory_tesseracts
               SET cue_terms = ?, semantic_axis = ?, relational_axis = ?,
                   temporal_axis = ?, epistemic_axis = ?, metadata = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                json.dumps(cue_terms),
                _clamp_01(semantic_axis),
                _clamp_01(relational_axis),
                _clamp_01(temporal_axis),
                _clamp_01(epistemic_axis),
                json.dumps(metadata or {}),
                now,
                tesseract_id,
            ),
        )
        conn.commit()

    return get_memory_tesseract_by_id(tesseract_id)


def get_memory_tesseract_by_id(tesseract_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM memory_tesseracts WHERE id = ?", (tesseract_id,)).fetchone()
    return dict(row) if row else None


def list_memory_tesseracts(limit: int = 200) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM memory_tesseracts ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def increment_memory_tesseract_usage(tesseract_id: str, amount: int = 1) -> None:
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """UPDATE memory_tesseracts
               SET usage_count = usage_count + ?, updated_at = ?
               WHERE id = ?""",
            (max(1, amount), now, tesseract_id),
        )
        conn.commit()


def add_memory_tesseract_link(
    tesseract_id: str,
    target_type: str,
    target_id: str,
    weight: float = 1.0,
) -> str:
    normalized_target_type = str(target_type).strip().lower()
    if normalized_target_type not in TESSERACT_TARGET_TYPES:
        raise ValueError(
            f"invalid target_type: {target_type}. allowed: {sorted(TESSERACT_TARGET_TYPES)}"
        )

    link_id = str(uuid.uuid4())
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memory_tesseract_links
               (id, tesseract_id, target_type, target_id, weight, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (link_id, tesseract_id, normalized_target_type, target_id, max(0.0, float(weight)), now),
        )
        conn.commit()
    return link_id


def get_memory_tesseract_links(
    tesseract_id: str,
    target_type: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM memory_tesseract_links WHERE tesseract_id = ?"
    params: list[Any] = [tesseract_id]
    if target_type:
        query += " AND target_type = ?"
        params.append(target_type)
    query += " ORDER BY weight DESC, created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_memory_tesseract_link_counts() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT tesseract_id, COUNT(*) as link_count
               FROM memory_tesseract_links
               GROUP BY tesseract_id"""
        ).fetchall()
    return {str(r["tesseract_id"]): int(r["link_count"]) for r in rows}


# ── Tensions ──────────────────────────────────────────────────────────────────

def upsert_tension(
    belief_id: str,
    oscillation_count: int,
    amplitude: float,
    stability_score: float,
    tension_reason: str,
) -> str:
    now = time.time()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM tensions WHERE belief_id = ? AND resolved = 0",
            (belief_id,),
        ).fetchone()

        if existing:
            tension_id = existing["id"]
            conn.execute(
                """UPDATE tensions
                   SET oscillation_count = ?, amplitude = ?, stability_score = ?,
                       tension_reason = ?, flagged_at = ?
                   WHERE id = ?""",
                (oscillation_count, amplitude, stability_score, tension_reason, now, tension_id),
            )
        else:
            tension_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO tensions
                   (id, belief_id, oscillation_count, amplitude, stability_score,
                    tension_reason, flagged_at, resolved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                (tension_id, belief_id, oscillation_count, amplitude, stability_score, tension_reason, now),
            )
        conn.commit()
    return tension_id


def get_tensions(resolved: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM tensions WHERE resolved = ? ORDER BY flagged_at DESC",
            (int(resolved),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tension_for_belief(belief_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tensions WHERE belief_id = ? AND resolved = 0",
            (belief_id,),
        ).fetchone()
    return dict(row) if row else None


def get_tension_by_id(tension_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tensions WHERE id = ?", (tension_id,)).fetchone()
    return dict(row) if row else None


def resolve_tension(tension_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE tensions SET resolved = 1 WHERE id = ?", (tension_id,))
        conn.commit()


# ── Stats helpers ─────────────────────────────────────────────────────────────

def count_archived_since(cutoff_ts: float) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM beliefs WHERE state = 'archived' AND last_reinforced >= ?",
            (cutoff_ts,),
        ).fetchone()
    return row["c"] if row else 0


def memory_count() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()
    return row["c"] if row else 0


def memory_count_since(cutoff_ts: float) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM memories WHERE created_at >= ?",
            (cutoff_ts,),
        ).fetchone()
    return row["c"] if row else 0
