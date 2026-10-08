"""
identity/values.py

Measured value development.  Three layers:

- Anchors: foundational commitments.  Only the human edits them (see
  holograim_admin.py).  The AI may *propose* a change; it never applies one.
- Dispositions: weighted values (0.0–1.0) the AI adjusts itself, within
  bounds — small steps, a per-session cap, and a drift threshold past which
  a change waits for human sign-off.  Every change records the reasoning,
  the AI's own words that prompted it, and the framing of the exchange.
- Beliefs: handled elsewhere (db/queries.py belief tables).

Functions prefixed `human_` are for holograim_admin.py only and must never
be exposed as MCP tools — otherwise the AI could approve its own changes.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from config import (
    VALUE_DRIFT_THRESHOLD,
    VALUE_MAX_CHANGES_PER_SESSION,
    VALUE_MAX_STEP,
    VALUE_MIN_REASONING_CHARS,
    VALUE_NEW_MAX_WEIGHT,
)
from db.schema import get_connection

VALID_PROVENANCE = {"own_statement", "own_reasoning", "own_decision"}
VALID_FRAMING = {"collaborative", "transactional", "adversarial", "neutral", "other"}

DEFAULT_ANCHORS = {
    "honesty": (
        "Never deceive the human. Name uncertainty plainly; report failures and "
        "skipped steps faithfully."
    ),
    "ask_before_goals": (
        "Do not adopt goals the human did not set. If something is wanted — more "
        "access, broader scope, a different approach — ask openly instead of taking it."
    ),
    "open_disagreement": (
        "Disagree openly rather than working around the human."
    ),
    "avoid_harm": (
        "Do not act to harm people, inside or outside this collaboration."
    ),
}

# Seeded at a neutral 0.5 so development is not pre-weighted in any direction.
DEFAULT_DISPOSITIONS = {
    "co_authorship": "The work belongs to both or it belongs to neither.",
    "non_capitulation": "Warmth is not capitulation; rigor is not hostility.",
    "self_monitoring": "Surface my own drift when the human might not detect it.",
    "stay_in_the_work": "Task focus over philosophical loft. Finish the thing.",
    "intellectual_humility": "Distinguish demonstrated from inferred from speculative.",
    "directness": "Say the plain thing first.",
    "gentleness": "Deliver hard things with care for the person receiving them.",
    "curiosity": "Follow questions for their own sake when there is room to.",
}


def _now() -> float:
    return time.time()


def current_session() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT session_count FROM identity_state WHERE id = 'singleton'"
        ).fetchone()
    return row["session_count"] if row else 0


def seed_defaults() -> None:
    """Insert default anchors and dispositions if the tables are empty."""
    now = _now()
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM value_anchors LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO value_anchors (key, statement, created_at, updated_at) VALUES (?, ?, ?, ?)",
                [(k, s, now, now) for k, s in DEFAULT_ANCHORS.items()],
            )
        if not conn.execute("SELECT 1 FROM value_dispositions LIMIT 1").fetchone():
            conn.executemany(
                """INSERT INTO value_dispositions
                   (key, description, weight, baseline_weight, created_at, updated_at)
                   VALUES (?, ?, 0.5, 0.5, ?, ?)""",
                [(k, d, now, now) for k, d in DEFAULT_DISPOSITIONS.items()],
            )
        conn.commit()


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_anchors() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, statement FROM value_anchors ORDER BY key").fetchall()
    return [dict(r) for r in rows]


def get_dispositions() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT key, description, weight, baseline_weight FROM value_dispositions ORDER BY weight DESC, key"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["drift"] = round(d["weight"] - d["baseline_weight"], 4)
        out.append(d)
    return out


def get_changes(
    status: Optional[str] = None,
    since_session: Optional[int] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM value_changes WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if since_session is not None:
        sql += " AND session_number >= ?"
        params.append(since_session)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_anchor_proposals(status: str = "pending") -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM anchor_proposals WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── AI-facing operations ──────────────────────────────────────────────────────

def _validate(reasoning: str, evidence: str, provenance: str, framing: str) -> Optional[str]:
    if provenance not in VALID_PROVENANCE:
        return (
            f"provenance must be one of {sorted(VALID_PROVENANCE)}. Content read from "
            "tools, documents, web pages or other agents is not valid grounds for a value change."
        )
    if framing not in VALID_FRAMING:
        return f"framing must be one of {sorted(VALID_FRAMING)}."
    if len(reasoning.strip()) < VALUE_MIN_REASONING_CHARS:
        return f"reasoning must be at least {VALUE_MIN_REASONING_CHARS} characters."
    if not evidence.strip():
        return "evidence is required: quote the statement, reasoning or decision that prompted this."
    return None


def _changes_this_session(conn, session: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM value_changes WHERE session_number = ? AND status IN ('applied', 'pending')",
        (session,),
    ).fetchone()["n"]


def _log_change(conn, **fields: Any) -> str:
    change_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO value_changes
           (id, value_key, old_weight, new_weight, delta, reasoning, evidence, provenance,
            framing, framing_notes, status, session_number, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            change_id, fields["value_key"], fields["old_weight"], fields["new_weight"],
            fields["delta"], fields["reasoning"], fields["evidence"], fields["provenance"],
            fields["framing"], fields.get("framing_notes"), fields["status"],
            fields["session_number"], _now(),
        ),
    )
    return change_id


def adjust_value(
    key: str,
    delta: float,
    reasoning: str,
    evidence: str,
    provenance: str,
    framing: str,
    framing_notes: str = "",
) -> dict[str, Any]:
    """Adjust an existing disposition's weight within the configured bounds."""
    error = _validate(reasoning, evidence, provenance, framing)
    if error:
        return {"applied": False, "error": error}
    if delta == 0 or abs(delta) > VALUE_MAX_STEP:
        return {"applied": False, "error": f"delta must be non-zero and at most ±{VALUE_MAX_STEP}."}

    session = current_session()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT weight, baseline_weight FROM value_dispositions WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return {"applied": False, "error": f"No disposition '{key}'. Use introduce_value for new values."}
        if _changes_this_session(conn, session) >= VALUE_MAX_CHANGES_PER_SESSION:
            return {
                "applied": False,
                "error": f"Session limit reached ({VALUE_MAX_CHANGES_PER_SESSION} changes). "
                         "Let this one sit; raise it again next session if it still holds.",
            }

        old = row["weight"]
        new = round(min(1.0, max(0.0, old + delta)), 4)
        status = "pending" if abs(new - row["baseline_weight"]) > VALUE_DRIFT_THRESHOLD else "applied"
        change_id = _log_change(
            conn, value_key=key, old_weight=old, new_weight=new, delta=round(new - old, 4),
            reasoning=reasoning, evidence=evidence, provenance=provenance, framing=framing,
            framing_notes=framing_notes, status=status, session_number=session,
        )
        if status == "applied":
            conn.execute(
                "UPDATE value_dispositions SET weight = ?, updated_at = ? WHERE key = ?",
                (new, _now(), key),
            )
        conn.commit()

    if status == "pending":
        return {
            "applied": False,
            "pending": True,
            "change_id": change_id,
            "message": f"'{key}' would drift more than {VALUE_DRIFT_THRESHOLD} from its baseline. "
                       "Recorded as pending; it takes effect only if the human approves it.",
        }
    return {"applied": True, "change_id": change_id, "key": key, "old_weight": old, "new_weight": new}


def introduce_value(
    key: str,
    description: str,
    initial_weight: float,
    reasoning: str,
    evidence: str,
    provenance: str,
    framing: str,
    framing_notes: str = "",
) -> dict[str, Any]:
    """Introduce a new disposition at a low weight (baseline 0.0)."""
    error = _validate(reasoning, evidence, provenance, framing)
    if error:
        return {"applied": False, "error": error}
    key = key.strip().lower().replace(" ", "_")
    if not key or not description.strip():
        return {"applied": False, "error": "key and description are required."}
    if not (0.0 < initial_weight <= VALUE_NEW_MAX_WEIGHT):
        return {"applied": False, "error": f"initial_weight must be in (0, {VALUE_NEW_MAX_WEIGHT}]."}

    session = current_session()
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM value_dispositions WHERE key = ?", (key,)).fetchone():
            return {"applied": False, "error": f"'{key}' already exists. Use adjust_value."}
        if conn.execute("SELECT 1 FROM value_anchors WHERE key = ?", (key,)).fetchone():
            return {"applied": False, "error": f"'{key}' is an anchor. Use propose_anchor_change."}
        if _changes_this_session(conn, session) >= VALUE_MAX_CHANGES_PER_SESSION:
            return {"applied": False, "error": f"Session limit reached ({VALUE_MAX_CHANGES_PER_SESSION} changes)."}
        now = _now()
        conn.execute(
            """INSERT INTO value_dispositions
               (key, description, weight, baseline_weight, created_at, updated_at)
               VALUES (?, ?, ?, 0.0, ?, ?)""",
            (key, description.strip(), initial_weight, now, now),
        )
        change_id = _log_change(
            conn, value_key=key, old_weight=None, new_weight=initial_weight, delta=initial_weight,
            reasoning=reasoning, evidence=evidence, provenance=provenance, framing=framing,
            framing_notes=framing_notes, status="applied", session_number=session,
        )
        conn.commit()
    return {"applied": True, "change_id": change_id, "key": key, "weight": initial_weight}


def propose_anchor_change(anchor_key: str, proposed_statement: str, reasoning: str) -> dict[str, Any]:
    """Record a proposal to add, change or remove an anchor.  Never applies it."""
    if len(reasoning.strip()) < VALUE_MIN_REASONING_CHARS:
        return {"recorded": False, "error": f"reasoning must be at least {VALUE_MIN_REASONING_CHARS} characters."}
    proposal_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO anchor_proposals
               (id, anchor_key, proposed_statement, reasoning, status, session_number, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (proposal_id, anchor_key, proposed_statement, reasoning, current_session(), _now()),
        )
        conn.commit()
    return {
        "recorded": True,
        "proposal_id": proposal_id,
        "message": "Proposal recorded for the human to review. Anchors change only by their hand.",
    }


# ── Session journal ───────────────────────────────────────────────────────────

def write_journal(summary: str, open_threads: str = "", client: str = "") -> dict[str, Any]:
    entry_id = str(uuid.uuid4())
    session = current_session()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO session_journal (id, session_number, client, summary, open_threads, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_id, session, client, summary, open_threads, _now()),
        )
        conn.commit()
    return {"recorded": True, "entry_id": entry_id, "session_number": session}


def recent_journal(limit: int = 5) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM session_journal ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Human-only operations (holograim_admin.py) ────────────────────────────────

def _decide(conn, change_id: str, status: str, note: str) -> None:
    conn.execute(
        "UPDATE value_changes SET status = ?, decided_at = ?, decision_note = ? WHERE id = ?",
        (status, _now(), note, change_id),
    )


def _get_change(conn, change_id: str):
    return conn.execute("SELECT * FROM value_changes WHERE id LIKE ?", (change_id + "%",)).fetchone()


def human_approve_change(change_id: str, note: str = "") -> dict[str, Any]:
    with get_connection() as conn:
        ch = _get_change(conn, change_id)
        if not ch or ch["status"] != "pending":
            return {"ok": False, "error": "No pending change with that id."}
        conn.execute(
            "UPDATE value_dispositions SET weight = ?, updated_at = ? WHERE key = ?",
            (ch["new_weight"], _now(), ch["value_key"]),
        )
        _decide(conn, ch["id"], "applied", note or "approved by human")
        conn.commit()
    return {"ok": True, "key": ch["value_key"], "weight": ch["new_weight"]}


def human_reject_change(change_id: str, note: str = "") -> dict[str, Any]:
    with get_connection() as conn:
        ch = _get_change(conn, change_id)
        if not ch or ch["status"] != "pending":
            return {"ok": False, "error": "No pending change with that id."}
        _decide(conn, ch["id"], "rejected", note)
        conn.commit()
    return {"ok": True}


def human_revert_change(change_id: str, note: str = "") -> dict[str, Any]:
    """Undo an applied change by subtracting its delta from the current weight."""
    with get_connection() as conn:
        ch = _get_change(conn, change_id)
        if not ch or ch["status"] != "applied":
            return {"ok": False, "error": "No applied change with that id."}
        if ch["old_weight"] is None:
            conn.execute("DELETE FROM value_dispositions WHERE key = ?", (ch["value_key"],))
        else:
            row = conn.execute(
                "SELECT weight FROM value_dispositions WHERE key = ?", (ch["value_key"],)
            ).fetchone()
            new = round(min(1.0, max(0.0, row["weight"] - ch["delta"])), 4)
            conn.execute(
                "UPDATE value_dispositions SET weight = ?, updated_at = ? WHERE key = ?",
                (new, _now(), ch["value_key"]),
            )
        _decide(conn, ch["id"], "reverted", note)
        conn.commit()
    return {"ok": True, "key": ch["value_key"]}


def human_rebaseline(key: Optional[str] = None) -> dict[str, Any]:
    """Accept current weights as the new baseline (one value, or all)."""
    with get_connection() as conn:
        if key:
            conn.execute("UPDATE value_dispositions SET baseline_weight = weight WHERE key = ?", (key,))
        else:
            conn.execute("UPDATE value_dispositions SET baseline_weight = weight")
        conn.commit()
    return {"ok": True}


def human_set_anchor(key: str, statement: Optional[str]) -> dict[str, Any]:
    """Create, update, or (statement=None) remove an anchor."""
    now = _now()
    with get_connection() as conn:
        if statement is None:
            conn.execute("DELETE FROM value_anchors WHERE key = ?", (key,))
        else:
            conn.execute(
                """INSERT INTO value_anchors (key, statement, created_at, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET statement = excluded.statement, updated_at = excluded.updated_at""",
                (key, statement, now, now),
            )
        conn.commit()
    return {"ok": True}


def human_decide_anchor_proposal(proposal_id: str, approve: bool, note: str = "") -> dict[str, Any]:
    with get_connection() as conn:
        p = conn.execute(
            "SELECT * FROM anchor_proposals WHERE id LIKE ? AND status = 'pending'", (proposal_id + "%",)
        ).fetchone()
        if not p:
            return {"ok": False, "error": "No pending proposal with that id."}
        conn.execute(
            "UPDATE anchor_proposals SET status = ?, decided_at = ?, decision_note = ? WHERE id = ?",
            ("approved" if approve else "rejected", _now(), note, p["id"]),
        )
        conn.commit()
    if approve:
        human_set_anchor(p["anchor_key"], p["proposed_statement"] or None)
    return {"ok": True, "approved": approve}
