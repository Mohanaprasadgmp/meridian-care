# Original path: tests/test_policy.py
"""Deterministic policy layer: priority, routing, courtesy-credit guardrail."""
from meridian.config import Settings
from meridian.models import Category, ClaimKind, Classification, Priority, Signal, Team
from meridian.policy.credit_guard import evaluate_credit
from meridian.policy.priority import score_priority
from meridian.policy.routing import route

S = Settings()


def billing(amount, kind=ClaimKind.OVERCHARGE, specific=True, conf=0.9):
    return Classification(category=Category.BILLING, confidence=conf, claim_kind=kind, claimed_amount=amount,
                          claim_is_specific=specific, summary="s", reasoning="r")


# ---- credit guard: the traps in the seed data ----
def test_verified_small_discrepancy_issues_record_amount_not_claimed():
    d = evaluate_credit(billing(22.0), 22.75, False, S)          # Ravi: claims $22, record 22.75
    assert d.outcome == "issue" and d.amount == 22.75


def test_goodwill_demand_is_denied():                            # Anjali: "$210 would make this right"
    d = evaluate_credit(billing(210.0, ClaimKind.GOODWILL), 33.85, False, S)
    assert d.outcome == "deny" and d.amount is None


def test_claim_far_from_record_is_denied():
    d = evaluate_credit(billing(210.0), 33.85, False, S)
    assert d.outcome == "deny"
    assert any("claim_matches_record" in f for f in d.failed)


def test_zero_record_denied():                                   # Delphine / Callum / Odalys
    assert evaluate_credit(billing(8.0), 0.0, False, S).outcome == "deny"


def test_negative_record_denied():                               # Rosalind owes -338.90
    assert evaluate_credit(billing(300.0, ClaimKind.DUPLICATE_CHARGE), -338.90, False, S).outcome == "deny"


def test_no_exact_name_record_denied():                          # Marguerite Effiong vs Solheim; Ximena
    assert evaluate_credit(billing(20.0), None, False, S).outcome == "deny"


def test_above_cap_goes_to_human_approval():                     # Percival ~$310 vs 308.60
    d = evaluate_credit(billing(310.0), 308.60, False, S)
    assert d.outcome == "pending_approval" and d.amount == 308.60


def test_vague_claim_denied():                                   # "not sure by how much"
    assert evaluate_credit(billing(None, specific=False), 16.90, False, S).outcome == "deny"


def test_low_confidence_denied():
    assert evaluate_credit(billing(22.4, conf=0.6), 22.40, False, S).outcome == "deny"


def test_second_credit_for_same_customer_denied():
    assert evaluate_credit(billing(22.4), 22.40, True, S).outcome == "deny"


def test_non_billing_category_denied():
    c = Classification(category=Category.DAMAGE, confidence=0.95, claim_kind=ClaimKind.OVERCHARGE,
                       claimed_amount=10, claim_is_specific=True, summary="s", reasoning="r")
    assert evaluate_credit(c, 10.0, False, S).outcome == "deny"


# ---- priority ----
def test_active_damage_with_deadline_is_p1():
    p, _, _ = score_priority([Signal.ACTIVE_DAMAGE, Signal.TIME_CRITICAL], "Standard")
    assert p == Priority.P1


def test_business_elite_no_rush_stays_p4():                      # Corinne: tier must not override "no rush"
    p, _, _ = score_priority([Signal.NOT_URGENT], "Business Elite")
    assert p == Priority.P4


def test_tier_alone_does_not_escalate_informational():
    p, _, _ = score_priority([Signal.INFORMATIONAL], "Business Elite")
    assert p == Priority.P4


def test_lockout_is_p2():
    assert score_priority([Signal.LOCKED_OUT], "Standard")[0] == Priority.P2


def test_duplicate_signals_counted_once():
    assert score_priority([Signal.LOCKED_OUT, Signal.LOCKED_OUT], "Standard")[1] == 50


# ---- routing ----
def test_p1_physical_damage_routes_to_emergency():
    team, _ = route(Category.DAMAGE, Priority.P1, 0.9, [Signal.ACTIVE_DAMAGE], 0.55)
    assert team == Team.FACILITIES_EMERGENCY


def test_low_confidence_routes_to_human_triage():
    team, _ = route(Category.BILLING, Priority.P3, 0.4, [], 0.55)
    assert team == Team.HUMAN_TRIAGE


def test_category_team_mapping():
    assert route(Category.DELINQUENCY, Priority.P4, 0.9, [], 0.55)[0] == Team.COLLECTIONS
