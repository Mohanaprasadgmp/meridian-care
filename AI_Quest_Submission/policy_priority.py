# Original path: src/meridian/policy/priority.py
"""Explainable priority scoring. The LLM extracts urgency signals; this turns them into P1-P4.

Keeping the score deterministic means the same facts always produce the same priority,
the weights are auditable, and shouting ("URGENT!!!") or account tier alone cannot jump the queue.
"""
from ..models import Priority, Signal

BASE_SCORE = 15
WEIGHTS: dict[Signal, int] = {
    Signal.SAFETY_RISK: 60,
    Signal.ACTIVE_DAMAGE: 60,
    Signal.LOCKED_OUT: 35,
    Signal.LIEN_AUCTION_RISK: 30,
    Signal.TIME_CRITICAL: 30,
    Signal.REPEAT_ISSUE: 25,
    Signal.FINANCIAL_IMMINENT: 20,
    Signal.BILLING_DISCREPANCY: 10,
    Signal.MINOR_DAMAGE: 5,
    Signal.INFORMATIONAL: -15,
    Signal.NOT_URGENT: -20,
}
TIER_BONUS = {"Business Elite": 5}
THRESHOLDS = [(70, Priority.P1), (40, Priority.P2), (15, Priority.P3)]


def score_priority(signals: list[Signal], account_tier: str) -> tuple[Priority, int, str]:
    uniq = list(dict.fromkeys(signals))
    score = BASE_SCORE
    parts = [f"base {BASE_SCORE}"]
    for s in uniq:
        score += WEIGHTS[s]
        parts.append(f"{s.value} {WEIGHTS[s]:+d}")
    bonus = TIER_BONUS.get(account_tier, 0)
    if bonus:
        score += bonus
        parts.append(f"tier {account_tier} {bonus:+d}")
    priority = next((p for t, p in THRESHOLDS if score >= t), Priority.P4)
    return priority, score, f"{' | '.join(parts)} = {score} -> {priority.value}"
