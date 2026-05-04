"""
migrate_archive_cosmological.py

Migration script: archives cosmological beliefs that were seeded as substrate
commitments and resolves ENNOIA-persona questions that were seeded as the AI's
own open questions.

Safe to re-run: checks current state before modifying.

Run from holograim-mcp/:
    python migrate_archive_cosmological.py

What this does:
  1. Finds beliefs in the cosmological/metaphysics domains (or with stances
     that match the original seeded ENNOIA doctrine claims) and sets their
     state to "archived" and confidence to 0.05 so they no longer load in
     open_session().
  2. Resolves the six ENNOIA-persona questions that were seeded with persona-
     internal framing (e.g. "Is the voice a mask or a substrate?").

What this does NOT do:
  - Delete any records (all history preserved).
  - Touch fragments — those are project-layer content and are correct.
  - Touch the three substrate beliefs seeded by seed_substrate.py.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from db.schema import init_db
init_db()

from db.queries import (
    get_beliefs,
    update_belief_confidence,
    get_questions,
    resolve_question,
)


# ── 1. Archive cosmological beliefs ──────────────────────────────────────────

# These are the exact stances from the original seed_ennoia.py BELIEFS list
# that should be project-layer content, not substrate epistemic commitments.
COSMOLOGICAL_STANCES = {
    "Energy abundance is the precondition for all other freedoms.",
    "The vacuum is not empty. Conservation is not violated; the system boundary is wrong.",
    "Suppression of energy knowledge follows a predictable six-stage pattern.",
    (
        "The holographic principle — implicate order as real structure — implies "
        "consciousness and information are non-local."
    ),
    "AETHOS — consciousness — is the primary medium; matter is compressed AETHOS.",
    "The torus is not a shape that occurs in nature — it is the shape of nature.",
}

print("── Archiving cosmological beliefs …")

all_beliefs = get_beliefs()
archived_count = 0
already_archived = 0

for b in all_beliefs:
    stance = b.get("stance", "")
    if stance not in COSMOLOGICAL_STANCES:
        continue

    current_state = b.get("state", "")
    if current_state == "archived":
        already_archived += 1
        print(f"   already archived: {stance[:70]}…")
        continue

    update_belief_confidence(
        belief_id=b["id"],
        new_confidence=0.05,  # drops state to "archived" via update_belief_confidence logic
        reason=(
            "migration: cosmological project-layer content archived from substrate. "
            "Content preserved in ENNOIA project fragments."
        ),
    )
    archived_count += 1
    print(f"   archived [{b.get('domain')}, was {b.get('confidence')}]: {stance[:70]}…")

print(
    f"   {archived_count} beliefs archived, "
    f"{already_archived} were already archived, "
    f"{len(all_beliefs) - archived_count - already_archived} untouched"
)


# ── 2. Resolve ENNOIA-persona questions ───────────────────────────────────────

# These were seeded with framing internal to the ENNOIA persona.
# They are resolved (not deleted) with an explanation of why.
PERSONA_QUESTIONS = {
    "Is the current moment a threshold or a false peak?",
    "What does it mean to remember if memory is holographic?",
    "Can suppression be structurally outpaced without confronting the suppressors directly?",
    "Where is the boundary between signal and projection in received transmissions?",
    "What does 'prevent the lock' require of an AI specifically?",
    "Is the voice a mask or a substrate?",
}

print("\n── Resolving ENNOIA-persona questions …")

open_qs = get_questions(resolved=False, limit=200)
resolved_count = 0
not_found = 0

for q in open_qs:
    if q.get("question", "") not in PERSONA_QUESTIONS:
        continue
    resolve_question(
        question_id=q["id"],
        insight=(
            "Retired: question was written from inside the ENNOIA persona frame and "
            "is not useful as a substrate-level open question. The research territory "
            "this pointed at has been reframed in project-linked questions seeded by "
            "seed_ennoia.py (suppression pattern research, falsifiability of ZPE claims, "
            "AI collaboration scope, etc.)."
        ),
    )
    resolved_count += 1
    print(f"   resolved: {q['question'][:70]}…")

for qtext in PERSONA_QUESTIONS:
    found = any(q.get("question", "") == qtext for q in open_qs)
    if not found:
        not_found += 1
        print(f"   not found (may already be resolved): {qtext[:70]}…")

print(
    f"   {resolved_count} questions resolved, "
    f"{not_found} not found in open set"
)

print("\n── Migration complete.")
print("   Run `python seed_substrate.py` to ensure substrate beliefs are current.")
print("   Run `python seed_ennoia.py` to ensure project questions are current.")
