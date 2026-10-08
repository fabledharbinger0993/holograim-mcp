"""
wakeup_brief.py

Opens a session and prints "the tape": anchors, dispositions, recent value
changes, pending decisions and the session journal, as Markdown.

Run by a Claude Code SessionStart hook so every session starts aligned with
the latest installment. Also refreshes data/MEMORY_VIEW.md for the human.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.schema import init_db
from identity.values import seed_defaults

init_db()
seed_defaults()

from identity.self_model import open_session
from identity.view import render_brief, write_view

session = open_session()
print(render_brief(session["session_count"]))
print(
    "\n---\nThis session is already open in HologrA.I.m; there is no need to call `wakeup`. "
    "Adjust values only via `adjust_value` / `introduce_value` when your own engagement "
    "genuinely moves you, and call `journal_session` before a meaningful session ends."
)
write_view()
