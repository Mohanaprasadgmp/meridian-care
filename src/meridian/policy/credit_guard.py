"""Courtesy-credit guardrail. Pure function: every rule is explicit, testable and reported back.

Non-negotiables:
  * the credited amount is ALWAYS the verified record amount, never the tenant's or the LLM's number
  * billing records are matched on exact customer name only (no fuzzy matching)
  * anything above the auto-cap becomes a pending approval for a human, never an auto-issue
"""
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..models import Category, ClaimKind, Classification

ELIGIBLE_KINDS = {ClaimKind.OVERCHARGE, ClaimKind.UNDERCREDIT, ClaimKind.DUPLICATE_CHARGE}


@dataclass
class CreditDecision:
    outcome: str                      # "issue" | "pending_approval" | "deny"
    amount: Optional[float] = None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [f"{name}: {detail}" for name, ok, detail in self.checks if not ok]

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "amount": self.amount,
                "checks": [{"rule": n, "passed": ok, "detail": d} for n, ok, d in self.checks]}


def evaluate_credit(cls: Classification, record_amount: Optional[float], prior_credit_exists: bool,
                    settings: Settings) -> CreditDecision:
    d = CreditDecision(outcome="deny")
    claimed = cls.claimed_amount

    def check(name: str, ok: bool, detail: str) -> bool:
        d.checks.append((name, ok, detail))
        return ok

    check("category_is_billing", cls.category == Category.BILLING, cls.category.value)
    check("confidence", cls.confidence >= settings.credit_min_confidence,
          f"{cls.confidence:.2f} (min {settings.credit_min_confidence})")
    check("claim_kind_eligible", cls.claim_kind in ELIGIBLE_KINDS,
          f"{cls.claim_kind.value}; goodwill/compensation requests are never auto-credited")
    check("claim_is_specific", cls.claim_is_specific and claimed is not None and claimed > 0,
          f"specific={cls.claim_is_specific}, claimed={claimed}")
    check("billing_record_exact_match", record_amount is not None,
          "record found" if record_amount is not None else "no billing record for this exact customer name")
    if record_amount is not None:
        check("record_confirms_discrepancy", record_amount > 0,
              f"verified_discrepancy={record_amount:.2f}" + (" (tenant owes balance)" if record_amount < 0 else ""))
        if claimed:
            tol = max(settings.credit_abs_tolerance, settings.credit_rel_tolerance * abs(record_amount))
            check("claim_matches_record", abs(claimed - record_amount) <= tol,
                  f"|{claimed:.2f} - {record_amount:.2f}| = {abs(claimed - record_amount):.2f} (tolerance {tol:.2f})")
    check("no_prior_credit", not prior_credit_exists, "customer already has a courtesy credit" if prior_credit_exists else "none")

    if d.failed:
        return d
    d.amount = round(record_amount, 2)
    if record_amount > settings.credit_auto_cap:
        d.checks.append(("within_auto_cap", False, f"{record_amount:.2f} > cap {settings.credit_auto_cap:.2f} -> human approval"))
        d.outcome = "pending_approval"
        return d
    d.checks.append(("within_auto_cap", True, f"{record_amount:.2f} <= cap {settings.credit_auto_cap:.2f}"))
    d.outcome = "issue"
    return d
