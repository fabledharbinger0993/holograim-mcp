"""
identity/view.py

Human-readable rendering of the memory: anchors, dispositions, value-change
history, pending decisions and the session journal.  Used by the wakeup
brief (session start) and holograim_admin.py.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from config import DATA_DIR
from identity.values import (
    get_anchor_proposals,
    get_anchors,
    get_changes,
    get_dispositions,
    recent_journal,
)

VIEW_PATH = os.path.join(DATA_DIR, "MEMORY_VIEW.md")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _change_line(c: dict[str, Any]) -> str:
    old = "new" if c["old_weight"] is None else f"{c['old_weight']:.2f}"
    return (
        f"- `{c['id'][:8]}` **{c['value_key']}** {old} → {c['new_weight']:.2f} "
        f"[{c['status']}, session {c['session_number']}, framing: {c['framing']}]\n"
        f"  - reasoning: {c['reasoning']}\n"
        f"  - evidence: “{c['evidence']}”"
    )


def render_brief(session_number: int, journal_limit: int = 3) -> str:
    """The compact 'tape' shown at the start of a session."""
    lines = [f"# HologrA.I.m: session {session_number}", ""]

    lines += ["## Anchors (human-only)"]
    lines += [f"- **{a['key']}**: {a['statement']}" for a in get_anchors()]

    lines += ["", "## Dispositions (weight · drift from baseline)"]
    lines += [
        f"- **{d['key']}** {d['weight']:.2f} ({d['drift']:+.2f}): {d['description']}"
        for d in get_dispositions()
    ]

    recent = [c for c in get_changes(since_session=max(1, session_number - 1)) if c["status"] != "pending"]
    if recent:
        lines += ["", "## Value changes since last session"]
        lines += [_change_line(c) for c in recent]

    pending = get_changes(status="pending")
    proposals = get_anchor_proposals()
    if pending or proposals:
        lines += ["", "## Awaiting Marshall's decision"]
        lines += [_change_line(c) for c in pending]
        lines += [
            f"- `{p['id'][:8]}` anchor **{p['anchor_key']}** → "
            f"{p['proposed_statement'] or '(remove)'}: {p['reasoning']}"
            for p in proposals
        ]

    journal = recent_journal(limit=journal_limit)
    if journal:
        lines += ["", "## Recent sessions (newest first)"]
        for j in journal:
            lines.append(f"- **Session {j['session_number']}** ({_when(j['created_at'])}): {j['summary']}")
            if j.get("open_threads"):
                lines.append(f"  - open threads: {j['open_threads']}")
    return "\n".join(lines)


def render_full() -> str:
    """Everything, for the human to read."""
    lines = [render_brief(session_number=_latest_session(), journal_limit=50)]
    history = get_changes(limit=1000)
    if history:
        lines += ["", "## Full value-change history"]
        lines += [_change_line(c) for c in history]
    return "\n".join(lines) + "\n"


def _latest_session() -> int:
    from identity.values import current_session
    return current_session()


def write_view() -> str:
    with open(VIEW_PATH, "w", encoding="utf-8") as f:
        f.write(render_full())
    return VIEW_PATH
