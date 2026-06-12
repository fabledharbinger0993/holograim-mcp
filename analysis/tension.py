"""
Belief oscillation and tension detection.

A tension is flagged when:
  - A belief has 3+ direction reversals in its revision history, OR
  - Amplitude > 2.0 confidence points across recent revisions
"""
from typing import Any
from db.queries import (
    get_belief_revisions,
    get_beliefs,
    upsert_tension,
    get_tension_for_belief,
)
from config import TENSION_OSCILLATION_MIN, TENSION_AMPLITUDE_FLAG


def _analyze_revisions(revisions: list[dict[str, Any]]) -> dict[str, Any]:
    if len(revisions) < 2:
        return {
            "oscillation_count": 0,
            "amplitude": 0.0,
            "stability_score": 1.0,
            "tension_reason": None,
        }

    direction_changes = 0
    last_direction = None

    confidences = [r["new_confidence"] for r in revisions]
    amplitude = max(confidences) - min(confidences)

    for i in range(len(revisions) - 1):
        prev_conf = revisions[i]["previous_confidence"] or revisions[i]["new_confidence"]
        curr_conf = revisions[i]["new_confidence"]
        direction = "up" if curr_conf >= prev_conf else "down"

        if last_direction is not None and direction != last_direction:
            direction_changes += 1
        last_direction = direction

    stability_score = max(0.0, 1.0 - min(1.0, direction_changes / 10.0))

    tension_reason: str | None = None
    if direction_changes >= TENSION_OSCILLATION_MIN:
        reasons = [r.get("reason", "") or "" for r in revisions[-3:]]
        reason_text = " | ".join(r for r in reasons if r)
        tension_reason = f"Oscillating belief ({direction_changes} reversals). Recent reasons: {reason_text}"
    elif amplitude > TENSION_AMPLITUDE_FLAG:
        tension_reason = f"High amplitude swing ({amplitude:.2f} confidence points)"

    return {
        "oscillation_count": direction_changes,
        "amplitude": round(amplitude, 4),
        "stability_score": round(stability_score, 4),
        "tension_reason": tension_reason,
    }


def analyze_belief_tension(belief_id: str) -> dict[str, Any]:
    """Analyze a single belief for tension and upsert if flagged."""
    revisions = get_belief_revisions(belief_id)
    analysis = _analyze_revisions(revisions)

    is_flagged = (
        analysis["oscillation_count"] >= TENSION_OSCILLATION_MIN
        or analysis["amplitude"] > TENSION_AMPLITUDE_FLAG
    )

    if is_flagged and analysis["tension_reason"]:
        tension_id = upsert_tension(
            belief_id=belief_id,
            oscillation_count=analysis["oscillation_count"],
            amplitude=analysis["amplitude"],
            stability_score=analysis["stability_score"],
            tension_reason=analysis["tension_reason"],
        )
        analysis["tension_id"] = tension_id
        analysis["flagged"] = True
    else:
        existing = get_tension_for_belief(belief_id)
        analysis["tension_id"] = existing["id"] if existing else None
        analysis["flagged"] = existing is not None

    return analysis


def run_tension_sweep() -> list[dict[str, Any]]:
    """Run tension analysis over all active/contested beliefs. Returns flagged results."""
    flagged = []
    for belief in get_beliefs():
        if belief["state"] in ("active", "contested", "forming"):
            result = analyze_belief_tension(belief["id"])
            if result.get("flagged"):
                flagged.append({"belief_id": belief["id"], "stance": belief["stance"], **result})
    return flagged
