# Original path: src/meridian/tools/environment.py
"""Tool execution environment for ONE request. Every call passes protocol + guardrail checks first.

Blocked calls are not exceptions: they return {"error": ...} to the model (so it can correct course)
and are recorded in the trace with allowed=False (so reviewers can see what was prevented).
"""
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import Action, BillingRecord, Classification as ClassificationRow, CourtesyCredit, Request
from ..models import ActionType, Category, Classification, Priority, RequestStatus, Team
from ..policy.credit_guard import CreditDecision, evaluate_credit
from ..policy.priority import score_priority
from ..policy.routing import route
from .definitions import TERMINAL_TOOLS

_money_lock = threading.Lock()   # serialises credit issuance across worker threads
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_PROMISE_RE = re.compile(r"\b(will|we'll|going to|have)\s+(be\s+)?(refund|credit|reimburs)", re.I)
_STOP = set("a an the i we our my me us you your is are was were be to of for on in at it this that and or "
            "but can could would please with about as by from so if not no any some have has had do does".split())
MAX_TENANT_MSG = 600


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return round(len(ta & tb) / len(ta | tb), 2) if ta and tb else 0.0


class GuardrailViolation(Exception):
    pass


@dataclass
class Triage:
    classification: Classification
    priority: Priority
    priority_score: int
    priority_explanation: str
    team: Team
    routing_explanation: str


@dataclass
class ToolEnvironment:
    session: Session
    request: Request
    run_id: int
    settings: Settings
    triage: Optional[Triage] = None
    called: set[str] = field(default_factory=set)
    billing_amount: Optional[float] = None
    billing_found: Optional[bool] = None
    credit: Optional[CreditDecision] = None
    terminal_action: Optional[str] = None

    # ---------------- dispatch ----------------
    def dispatch(self, name: str, args: dict[str, Any]) -> tuple[dict, bool]:
        """Returns (result, allowed)."""
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return {"error": f"unknown tool '{name}'"}, False
        try:
            if self.terminal_action:
                raise GuardrailViolation(f"a final action ({self.terminal_action}) was already taken; stop now")
            result = handler(args or {})
            self.called.add(name)
            if name in TERMINAL_TOOLS:
                self.terminal_action = name
            return result, True
        except GuardrailViolation as e:
            return {"error": f"blocked by guardrail: {e}"}, False
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            return {"error": f"invalid arguments: {e}"}, False

    # ---------------- protocol helpers ----------------
    def _require_classification(self) -> Triage:
        if not self.triage:
            raise GuardrailViolation("call record_classification first")
        return self.triage

    def _outstanding_steps(self) -> list[str]:
        steps = []
        if not self.triage:
            return ["record_classification"]
        c = self.triage.classification
        if "search_request_history" not in self.called:
            steps.append("search_request_history")
        if c.confidence < self.settings.context_confidence_threshold and "get_customer_context" not in self.called:
            steps.append("get_customer_context (confidence below threshold)")
        if c.category == Category.BILLING and c.claim_is_specific and "lookup_billing_record" not in self.called:
            steps.append("lookup_billing_record (specific billing claim)")
        return steps

    def _require_ready_for_final(self) -> Triage:
        t = self._require_classification()
        missing = self._outstanding_steps()
        if missing:
            raise GuardrailViolation(f"required steps not done: {missing}")
        return t

    def _check_tenant_message(self, msg: str) -> str:
        msg = (msg or "").strip()
        if not msg:
            raise GuardrailViolation("tenant_message is required")
        if len(msg) > MAX_TENANT_MSG:
            raise GuardrailViolation(f"tenant_message exceeds {MAX_TENANT_MSG} chars")
        issued = self.credit.amount if self.credit and self.credit.outcome == "issue" else None
        for m in _AMOUNT_RE.findall(msg):
            if issued is None or abs(float(m.replace(",", "")) - issued) > 0.005:
                raise GuardrailViolation(f"tenant_message mentions ${m}, which is not an issued credit amount")
        if issued is None and _PROMISE_RE.search(msg):
            raise GuardrailViolation("tenant_message promises a refund/credit that has not been issued")
        return msg

    def _record_action(self, action_type: ActionType, details: dict, tenant_message: Optional[str] = None) -> None:
        self.session.add(Action(request_id=self.request.request_id, run_id=self.run_id, action_type=action_type.value,
                                actor="agent", details=details, tenant_message=tenant_message))

    # ---------------- tools ----------------
    def _t_record_classification(self, args: dict) -> dict:
        c = Classification(**args)
        priority, score, p_expl = score_priority(c.urgency_signals, self.request.account_tier)
        team, r_expl = route(c.category, priority, c.confidence, c.urgency_signals, self.settings.human_triage_threshold)
        self.triage = Triage(c, priority, score, p_expl, team, r_expl)
        self.session.add(ClassificationRow(
            run_id=self.run_id, request_id=self.request.request_id, category=c.category.value,
            confidence=c.confidence, urgency_signals=[s.value for s in c.urgency_signals], language=c.language,
            claim_kind=c.claim_kind.value, claimed_amount=c.claimed_amount, claim_is_specific=c.claim_is_specific,
            summary=c.summary, reasoning=c.reasoning, priority=priority.value, priority_score=score,
            priority_explanation=p_expl, team=team.value, routing_explanation=r_expl))
        return {"recorded": True, "priority": priority.value, "priority_explanation": p_expl,
                "team": team.value, "routing_explanation": r_expl, "required_next_steps": self._outstanding_steps()}

    def _t_search_request_history(self, args: dict) -> dict:
        r = self.request
        others = self.session.scalars(
            select(Request).where(Request.customer_name == r.customer_name, Request.request_id != r.request_id)
            .order_by(Request.submitted_at)).all()
        items = [{
            "request_id": o.request_id, "status": o.status, "submitted_at": o.submitted_at.isoformat(),
            "submitted_before_this": o.submitted_at < r.submitted_at, "category": o.category,
            "text_similarity": similarity(r.body, o.body), "body": o.body,
        } for o in others]
        return {"customer_match": "exact name", "other_requests": items, "count": len(items),
                "note": "Identical wording from a different tenant is never a duplicate."}

    def _t_get_customer_context(self, args: dict) -> dict:
        self._require_classification()
        r = self.request
        reqs = self.session.scalars(select(Request).where(Request.customer_name == r.customer_name)).all()
        credits = self.session.scalars(select(CourtesyCredit).where(CourtesyCredit.customer_name == r.customer_name)).all()
        has_billing = self.session.get(BillingRecord, r.customer_name) is not None
        return {"account_tier": r.account_tier, "total_requests": len(reqs),
                "open_requests": [q.request_id for q in reqs if q.status in ("new", "open", "routed", "acknowledged") and q.request_id != r.request_id],
                "has_billing_discrepancy_record": has_billing,
                "prior_credits": [{"request_id": c.request_id, "amount": c.amount, "status": c.status} for c in credits],
                "guidance": "Re-call record_classification if this changes your view; otherwise proceed."}

    def _t_lookup_billing_record(self, args: dict) -> dict:
        t = self._require_classification()
        if t.classification.category != Category.BILLING:
            raise GuardrailViolation("billing records may only be read for Billing & Autopay Dispute requests")
        rec = self.session.get(BillingRecord, self.request.customer_name)
        self.billing_found = rec is not None
        self.billing_amount = rec.verified_discrepancy if rec else None
        if not rec:
            return {"found": False, "note": "No billing record for this exact customer name. Route to Billing Team."}
        return {"found": True, "verified_discrepancy": rec.verified_discrepancy,
                "meaning": "positive = amount owed to tenant; 0 = no discrepancy; negative = tenant owes Meridian"}

    def _t_issue_courtesy_credit(self, args: dict) -> dict:
        t = self._require_classification()
        if "lookup_billing_record" not in self.called:
            raise GuardrailViolation("call lookup_billing_record first")
        if self.credit is not None:
            raise GuardrailViolation("credit already evaluated for this request")
        c, r = t.classification, self.request
        with _money_lock:
            prior = self.session.scalar(select(CourtesyCredit).where(
                CourtesyCredit.customer_name == r.customer_name,
                CourtesyCredit.status.in_(["issued", "pending_approval", "offered"])))
            # A chat customer can confirm in the conversation, so a mismatched claim becomes an offer
            decision = evaluate_credit(c, self.billing_amount, prior is not None, self.settings,
                                       can_offer=(r.source == "chat" and r.conversation_id is not None))
            self.credit = decision
            rationale = (args.get("rationale") or "")[:1000]
            if decision.outcome in ("issue", "pending_approval", "offer"):
                status = {"issue": "issued", "pending_approval": "pending_approval", "offer": "offered"}[decision.outcome]
                self.session.add(CourtesyCredit(request_id=r.request_id, customer_name=r.customer_name,
                                                amount=decision.amount, claimed_amount=c.claimed_amount, status=status,
                                                decided_by="agent+policy", rationale=rationale))
                self._record_action(ActionType.COURTESY_CREDIT, {"status": status, **decision.as_dict()})
                self.session.commit()   # inside the lock: other workers' prior-credit check must see this row
        result = decision.as_dict()
        result["next"] = {"issue": "Call acknowledge_request confirming the credited amount.",
                          "pending_approval": "Amount exceeds auto-cap; call route_request so Billing can approve.",
                          "offer": "The claim differs from the verified record. The customer will be offered the verified "
                                   "amount in chat. Call route_request; do not mention amounts in tenant_message.",
                          "deny": "Do not promise money. Call route_request with the failed checks in the internal note."}[decision.outcome]
        return result

    def _t_acknowledge_request(self, args: dict) -> dict:
        t = self._require_ready_for_final()
        if not (self.credit and self.credit.outcome == "issue"):
            raise GuardrailViolation("acknowledge_request resolves a request and is only valid after an issued credit; use route_request")
        msg = self._check_tenant_message(args.get("tenant_message", ""))
        self._apply_triage(t, RequestStatus.RESOLVED, t.team)
        self._record_action(ActionType.ACKNOWLEDGE, {"resolution": "courtesy credit issued", "amount": self.credit.amount}, msg)
        return {"status": RequestStatus.RESOLVED.value}

    def _t_route_request(self, args: dict) -> dict:
        t = self._require_ready_for_final()
        msg = self._check_tenant_message(args.get("tenant_message", ""))
        note = (args.get("internal_note") or "")[:2000]
        status = RequestStatus.NEEDS_REVIEW if t.team == Team.HUMAN_TRIAGE else RequestStatus.ROUTED
        if self.credit and self.credit.outcome == "offer" and t.team != Team.HUMAN_TRIAGE:
            status = RequestStatus.AWAITING_CUSTOMER     # waiting for the customer to accept the offer
        self._apply_triage(t, status, t.team)
        self._record_action(ActionType.ROUTE, {"team": t.team.value, "priority": t.priority.value, "internal_note": note,
                                               "credit_outcome": self.credit.outcome if self.credit else None}, msg)
        return {"status": status.value, "team": t.team.value, "priority": t.priority.value}

    def _t_close_as_duplicate(self, args: dict) -> dict:
        t = self._require_ready_for_final()
        target_id = args.get("duplicate_of", "")
        target = self.session.get(Request, target_id)
        r = self.request
        if t.team == Team.HUMAN_TRIAGE:
            raise GuardrailViolation("low-confidence requests cannot be auto-closed")
        if target is None or target.request_id == r.request_id:
            raise GuardrailViolation(f"'{target_id}' is not another existing request")
        if target.customer_name != r.customer_name:
            raise GuardrailViolation("duplicates must belong to the same tenant (exact name)")
        if target.submitted_at >= r.submitted_at:
            raise GuardrailViolation("the original must be submitted before this request")
        if target.status in ("closed", "resolved", "closed_duplicate"):
            raise GuardrailViolation(f"{target_id} is {target.status}; a new report of a closed issue is a reopen, not a duplicate")
        if t.priority == Priority.P1 and target.priority not in (None, "P1"):
            raise GuardrailViolation("a P1 follow-up cannot be closed into a lower-priority ticket; route it instead")
        msg = self._check_tenant_message(args.get("tenant_message", ""))
        self._apply_triage(t, RequestStatus.CLOSED_DUPLICATE, t.team)
        r.duplicate_of = target.request_id
        self._record_action(ActionType.CLOSE_DUPLICATE, {"duplicate_of": target.request_id,
                                                         "reason": (args.get("reason") or "")[:1000]}, msg)
        self.session.add(Action(request_id=target.request_id, run_id=self.run_id, action_type="follow_up_note",
                                actor="agent", details={"note": f"Tenant followed up via {r.request_id}: {r.body[:300]}",
                                                        "repeat_contact": True}))
        return {"status": RequestStatus.CLOSED_DUPLICATE.value, "duplicate_of": target.request_id}

    def _apply_triage(self, t: Triage, status: RequestStatus, team: Team) -> None:
        r = self.request
        r.category, r.confidence = t.classification.category.value, t.classification.confidence
        r.priority, r.priority_score, r.team, r.status = t.priority.value, t.priority_score, team.value, status.value
