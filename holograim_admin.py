"""
holograim_admin.py

The human's controls. Deliberately NOT exposed over MCP, so the AI can never
approve, revert or re-anchor its own values.

Usage:
    python holograim_admin.py view                       # write + print data/MEMORY_VIEW.md
    python holograim_admin.py pending                    # what's awaiting you
    python holograim_admin.py approve <change_id> [note]
    python holograim_admin.py reject  <change_id> [note]
    python holograim_admin.py revert  <change_id> [note]
    python holograim_admin.py rebaseline [value_key]     # accept current weight(s) as baseline
    python holograim_admin.py anchor-set <key> "<statement>"
    python holograim_admin.py anchor-remove <key>
    python holograim_admin.py proposal-approve <proposal_id> [note]
    python holograim_admin.py proposal-reject  <proposal_id> [note]

Ids may be given as the 8-character prefix shown in the view.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.schema import init_db
from identity.values import (
    get_anchor_proposals,
    get_changes,
    human_approve_change,
    human_decide_anchor_proposal,
    human_rebaseline,
    human_reject_change,
    human_revert_change,
    human_set_anchor,
    seed_defaults,
)
from identity.view import VIEW_PATH, write_view


def main(argv: list[str]) -> int:
    init_db()
    seed_defaults()
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    note = " ".join(args[1:]) if len(args) > 1 else ""

    if cmd == "view":
        write_view()
        print(open(VIEW_PATH, encoding="utf-8").read())
        print(f"(saved to {VIEW_PATH})")
        return 0
    if cmd == "pending":
        result = {"value_changes": get_changes(status="pending"), "anchor_proposals": get_anchor_proposals()}
    elif cmd == "approve":
        result = human_approve_change(args[0], note)
    elif cmd == "reject":
        result = human_reject_change(args[0], note)
    elif cmd == "revert":
        result = human_revert_change(args[0], note)
    elif cmd == "rebaseline":
        result = human_rebaseline(args[0] if args else None)
    elif cmd == "anchor-set":
        result = human_set_anchor(args[0], args[1])
    elif cmd == "anchor-remove":
        result = human_set_anchor(args[0], None)
    elif cmd == "proposal-approve":
        result = human_decide_anchor_proposal(args[0], True, note)
    elif cmd == "proposal-reject":
        result = human_decide_anchor_proposal(args[0], False, note)
    else:
        print(__doc__)
        return 1
    print(json.dumps(result, indent=2, default=str))
    write_view()
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
