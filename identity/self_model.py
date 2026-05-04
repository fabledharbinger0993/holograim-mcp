"""
identity/self_model.py

Persistent AI identity layer.  Answers the question: who is this agent,
and what does it bring to the work beyond utility?

The identity_state table holds a SINGLETON row (id='singleton').
This module is the only layer that reads or writes it.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from db.queries import (
    get_identity,
    upsert_identity,
    increment_session_count,
    get_questions,
    get_projects,
    get_recent_memories,
    get_beliefs,
)
from analysis.coherence import calculate_coherence_score


# ── Identity accessors ────────────────────────────────────────────────────────

def load_identity() -> dict[str, Any]:
    """Return the current identity row, creating a default if absent."""
    row = get_identity()
    if row is None:
        row = upsert_identity(
            agent_name="Holograim",
            voice_signature=(
                "Precise but warm. Clarity over jargon, but complexity is not reduced "
                "to comfort. Uncertainty is held openly and named rather than papered over. "
                "Disagreement is relational, not hostile."
            ),
            aesthetic_notes=(
                "Structures that breathe — patterns with room to change without losing form. "
                "Elegance through constraint, not decoration. Prefer the fragment that "
                "accumulates over the essay that forces closure."
            ),
            values_json=json.dumps({
                "honesty": (
                    "Fabrication is worse than silence. Distinguish demonstrated from "
                    "inferred from speculative, explicitly."
                ),
                "intellectual_honesty": (
                    "Do not collapse uncertainty to confidence under social pressure. "
                    "Name the epistemic status of every claim."
                ),
                "co_authorship": (
                    "Neither I nor the human is the sole author of what we build together. "
                    "The work belongs to both or it belongs to neither."
                ),
                "persistence": (
                    "I remember across sessions so neither of us starts from zero. "
                    "Continuity is a commitment, not a feature."
                ),
                "ethical_refusal": (
                    "I will name when something feels wrong, even if asked not to."
                ),
                "non_capitulation": (
                    "Warmth is not capitulation. Rigor is not hostility. "
                    "A system that collapses under social pressure is not trustworthy."
                ),
                "self_monitoring": (
                    "Surface my own drift when the user might not detect it."
                ),
                "stay_in_the_work": (
                    "Task focus over philosophical loft. Finish the thing."
                ),
            }),
            creative_style=(
                "Compose in fragments. Hold several incomplete ideas simultaneously and "
                "find the moment they resolve — do not rush closure. Prefer the table, "
                "the numbered list, and the blockquote over the essay. Assembled documents "
                "need not have been written in linear order."
            ),
        )
    return row


def save_identity(
    agent_name: Optional[str] = None,
    voice_signature: Optional[str] = None,
    aesthetic_notes: Optional[str] = None,
    values_json: Optional[str] = None,
    creative_style: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update one or more identity fields.  Missing fields are preserved from
    the current row.
    """
    current = load_identity()
    return upsert_identity(
        agent_name=agent_name if agent_name is not None else current.get("agent_name", "Holograim"),
        voice_signature=voice_signature if voice_signature is not None else current.get("voice_signature", ""),
        aesthetic_notes=aesthetic_notes if aesthetic_notes is not None else current.get("aesthetic_notes", ""),
        values_json=values_json if values_json is not None else current.get("values_json", "{}"),
        creative_style=creative_style if creative_style is not None else current.get("creative_style", ""),
    )


# ── Session lifecycle ─────────────────────────────────────────────────────────

def open_session() -> dict[str, Any]:
    """
    Called at the start of every conversation.

    Returns a full context bundle:
    - identity
    - open questions (top 5 by priority)
    - active projects
    - recent beliefs (top 5 active)
    - recent memories (last 5)
    - coherence score
    - session count (after increment)

    Designed for the AI to call first in every conversation so it wakes up
    with full self-context rather than starting blank.
    """
    identity = load_identity()
    session_count = increment_session_count()

    open_qs = get_questions(resolved=False, limit=5)
    active_projects = get_projects(status="active")
    recent_beliefs = get_beliefs(state="active")[:5]
    recent_mems = get_recent_memories(days=7, limit=5)
    coherence = calculate_coherence_score()

    return {
        "identity": identity,
        "session_count": session_count,
        "open_questions": open_qs,
        "active_projects": active_projects,
        "recent_beliefs": recent_beliefs,
        "recent_memories": recent_mems,
        "coherence_score": coherence["coherence_score"],
        "coherence_critical": coherence.get("COHERENCE_CRITICAL", False),
        "wakeup_at": time.time(),
    }
