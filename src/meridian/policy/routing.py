"""Deterministic team routing from the (final) classification and priority."""
from ..models import Category, Priority, Signal, Team

CATEGORY_TEAM = {
    Category.BILLING: Team.BILLING,
    Category.GATE: Team.ACCESS,
    Category.TRANSFER: Team.RESERVATIONS,
    Category.DAMAGE: Team.CLAIMS,
    Category.DELINQUENCY: Team.COLLECTIONS,
}


def route(category: Category, priority: Priority, confidence: float, signals: list[Signal],
          human_triage_threshold: float) -> tuple[Team, str]:
    if confidence < human_triage_threshold:
        return Team.HUMAN_TRIAGE, f"confidence {confidence:.2f} < {human_triage_threshold} -> human triage"
    physical = {Signal.ACTIVE_DAMAGE, Signal.SAFETY_RISK} & set(signals)
    if priority == Priority.P1 and physical:
        return Team.FACILITIES_EMERGENCY, f"P1 with {sorted(s.value for s in physical)} -> on-site emergency response"
    team = CATEGORY_TEAM[category]
    return team, f"category '{category.value}' -> {team.value}"
