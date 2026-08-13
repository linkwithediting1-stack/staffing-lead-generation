from __future__ import annotations

from .models import Lead


def score_lead(lead: Lead, target_locations: list[str], target_industries: list[str]) -> Lead:
    score = 0
    reasons: list[str] = []

    if lead.hiring_signals:
        points = min(30, 12 + len(lead.hiring_signals) * 4)
        score += points
        reasons.append(f"{points} hiring-language evidence")
    if lead.job_urls:
        points = min(25, 8 + len(lead.job_urls) * 3)
        score += points
        reasons.append(f"{points} careers/job links")
    if _contains_any(lead.location, target_locations):
        score += 15
        reasons.append("15 target-location match")
    elif lead.location:
        score += 5
        reasons.append("5 location evidence")
    if _contains_any(lead.industry, target_industries):
        score += 12
        reasons.append("12 target-industry match")
    elif lead.industry:
        score += 4
        reasons.append("4 industry evidence")
    if lead.emails:
        score += 10
        reasons.append("10 public role-based business email")
    if lead.description:
        score += 4
        reasons.append("4 verified company description")
    if len(lead.evidence_urls) >= 2:
        score += 4
        reasons.append("4 multiple evidence pages")
    if not lead.hiring_signals and not lead.job_urls:
        score -= 15
        reasons.append("-15 no current hiring evidence")

    lead.score = max(0, min(100, score))
    lead.confidence = min(1.0, 0.25 + 0.12 * len(reasons))
    lead.score_reason = "; ".join(reasons) or "No qualifying evidence"
    lead.status = "review" if lead.score > 0 else "new"
    return lead


def _contains_any(value: str, targets: list[str]) -> bool:
    lowered = value.lower()
    return any(item.lower() in lowered for item in targets)

