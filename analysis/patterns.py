"""
Pattern aggregation and dominance tracking.

Aggregates:
  - Congress perspective dominance across recent sessions
  - Memory growth rate (memories added per day over last 7 days)
  - Belief stability distribution
  - Oscillating beliefs needing attention
"""
import time
from typing import Any
from db.queries import (
    get_recent_congress_logs,
    get_beliefs,
    get_tensions,
    memory_count,
    memory_count_since,
)

CONGRESS_ROLES = ["advocate", "skeptic", "synthesizer", "ethics"]


def _extract_dominant_role_from_log(log: dict[str, Any]) -> str | None:
    """
    Heuristic: the role whose position is most represented in ego_response.
    Falls back to counting keyword overlap.
    """
    ego = (log.get("ego_response") or "").lower()
    if not ego:
        return None

    role_positions = {
        "advocate": (log.get("advocate_position") or "").lower(),
        "skeptic": (log.get("skeptic_position") or "").lower(),
        "synthesizer": (log.get("synthesizer_position") or "").lower(),
        "ethics": (log.get("ethics_review") or "").lower(),
    }

    best_role = None
    best_overlap = -1

    for role, position in role_positions.items():
        if not position:
            continue
        # Count shared significant words (>4 chars)
        pos_words = set(w for w in position.split() if len(w) > 4)
        ego_words = set(w for w in ego.split() if len(w) > 4)
        overlap = len(pos_words & ego_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_role = role

    return best_role


def get_perspective_dominance(last_n: int = 10) -> dict[str, Any]:
    """
    Analyze last N congress sessions to determine which perspective dominated.
    Returns frequency counts and the overall dominant perspective.
    """
    logs = get_recent_congress_logs(limit=last_n)
    if not logs:
        return {
            "sessions_analyzed": 0,
            "dominant_perspective": None,
            "frequency": {role: 0 for role in CONGRESS_ROLES},
        }

    frequency: dict[str, int] = {role: 0 for role in CONGRESS_ROLES}

    for log in logs:
        dominant = _extract_dominant_role_from_log(log)
        if dominant and dominant in frequency:
            frequency[dominant] += 1

    dominant_perspective = max(frequency, key=lambda r: frequency[r])
    # If all zero, return None
    if all(v == 0 for v in frequency.values()):
        dominant_perspective = None

    return {
        "sessions_analyzed": len(logs),
        "dominant_perspective": dominant_perspective,
        "frequency": frequency,
    }


def get_memory_growth_rate() -> dict[str, Any]:
    """
    Returns memories added per day over the last 7 days.
    Also returns total memory count.
    """
    seven_days_ago = time.time() - 7 * 86400
    recent = memory_count_since(seven_days_ago)
    total = memory_count()
    rate = round(recent / 7.0, 2)
    return {
        "total_memories": total,
        "added_last_7_days": recent,
        "avg_per_day": rate,
    }


def get_oscillating_beliefs() -> list[dict[str, Any]]:
    """
    Return beliefs that have active unresolved tensions (needing attention).
    Joins tension data with belief metadata.
    """
    tensions = get_tensions(resolved=False)
    if not tensions:
        return []

    all_beliefs = {b["id"]: b for b in get_beliefs()}
    result = []

    for t in tensions:
        belief = all_beliefs.get(t["belief_id"])
        if belief:
            result.append({
                "belief_id": t["belief_id"],
                "stance": belief["stance"],
                "domain": belief["domain"],
                "confidence": belief["confidence"],
                "state": belief["state"],
                "oscillation_count": t["oscillation_count"],
                "amplitude": t["amplitude"],
                "stability_score": t["stability_score"],
                "tension_reason": t["tension_reason"],
            })

    result.sort(key=lambda x: x["oscillation_count"], reverse=True)
    return result


def get_belief_stability_distribution() -> dict[str, int]:
    """Count beliefs by state."""
    beliefs = get_beliefs()
    dist: dict[str, int] = {"forming": 0, "active": 0, "contested": 0, "archived": 0}
    for b in beliefs:
        state = b.get("state", "forming")
        if state in dist:
            dist[state] += 1
    return dist
