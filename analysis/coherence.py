"""
Coherence scoring.

Score = 100
  - 5 pts per contested belief
  - 3 pts per unresolved tension (oscillation_count >= 3)
  - 10 pts if any belief has amplitude > 3.0 in last 5 revisions
  - 2 pts per belief archived in last 30 days (rapid archival = instability)
Floor: 0 / Cap: 100
COHERENCE_CRITICAL flag set when score < 50.
"""
import time
from typing import Any
from db.queries import (
    get_beliefs,
    get_tensions,
    get_belief_revisions,
    count_archived_since,
)


def _max_amplitude_in_recent_revisions(belief_id: str, last_n: int = 5) -> float:
    revisions = get_belief_revisions(belief_id)[-last_n:]
    if len(revisions) < 2:
        return 0.0
    confidences = [r["new_confidence"] for r in revisions]
    return max(confidences) - min(confidences)


def calculate_coherence_score() -> dict[str, Any]:
    score = 100
    deductions: list[dict[str, Any]] = []

    # Contested beliefs
    contested = [b for b in get_beliefs(state="contested")]
    if contested:
        pts = len(contested) * 5
        score -= pts
        deductions.append({"reason": "contested_beliefs", "count": len(contested), "deduction": pts})

    # Unresolved tensions
    tensions = get_tensions(resolved=False)
    active_tensions = [t for t in tensions if t["oscillation_count"] >= 3]
    if active_tensions:
        pts = len(active_tensions) * 3
        score -= pts
        deductions.append({"reason": "unresolved_tensions", "count": len(active_tensions), "deduction": pts})

    # High-amplitude beliefs
    high_amp_beliefs = []
    for belief in get_beliefs():
        amp = _max_amplitude_in_recent_revisions(belief["id"])
        if amp > 3.0:
            high_amp_beliefs.append(belief["id"])
    if high_amp_beliefs:
        score -= 10
        deductions.append({"reason": "high_amplitude_beliefs", "count": len(high_amp_beliefs), "deduction": 10})

    # Rapid archival (last 30 days)
    cutoff = time.time() - 30 * 86400
    recent_archived = count_archived_since(cutoff)
    if recent_archived > 0:
        pts = recent_archived * 2
        score -= pts
        deductions.append({"reason": "rapid_archival_last_30d", "count": recent_archived, "deduction": pts})

    score = max(0, min(100, score))
    result: dict[str, Any] = {
        "coherence_score": score,
        "deductions": deductions,
    }
    if score < 50:
        result["COHERENCE_CRITICAL"] = True

    return result
