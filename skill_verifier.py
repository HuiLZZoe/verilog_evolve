"""Evidence gates for candidate RTL optimization skills."""

from __future__ import annotations

from typing import Any


def verify_skill_candidate(candidate: dict[str, Any], *, min_sec_pass: int = 2, min_promotions: int = 1) -> dict[str, Any]:
    evidence = candidate.get("evidence", {}) if isinstance(candidate.get("evidence"), dict) else {}
    sec_pass = int(evidence.get("sec_pass", 0) or 0)
    promotions = int(evidence.get("promoted", 0) or 0)
    avg_score_delta = evidence.get("avg_score_delta")
    has_positive_score = isinstance(avg_score_delta, (int, float)) and float(avg_score_delta) < 0
    accepted = sec_pass >= min_sec_pass and (promotions >= min_promotions or has_positive_score)
    reasons: list[str] = []
    if sec_pass < min_sec_pass:
        reasons.append(f"insufficient SEC-pass evidence: {sec_pass} < {min_sec_pass}")
    if promotions < min_promotions and not has_positive_score:
        reasons.append("no promotion or positive score-delta evidence")
    if candidate.get("equivalence_risk") == "high" and promotions < min_promotions:
        accepted = False
        reasons.append("high equivalence risk without promotion evidence")
    return {
        "accepted": accepted,
        "reasons": reasons or ["evidence threshold satisfied"],
        "score": sec_pass + promotions + (1 if has_positive_score else 0),
    }
