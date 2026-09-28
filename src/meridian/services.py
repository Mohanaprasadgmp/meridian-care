"""Human-in-the-loop operations. Every change is validated and written to the overrides audit log."""
import re
import threading
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import Action, CourtesyCredit, Override, Request, SessionLocal, utcnow
from .models import Category, Priority, RequestStatus, Team

EDITABLE = {"category": [c.value for c in Category], "priority": [p.value for p in Priority],
            "team": [t.value for t in Team], "status": [s.value for s in RequestStatus]}
ACCOUNT_TIERS = ["Standard", "Business Elite"]
MAX_BODY = 5000
_id_lock = threading.Lock()


def create_request(customer_name: str, account_tier: str, body: str, source: str = "csv",
                   customer_user_id: Optional[int] = None, conversation_id: Optional[str] = None,
                   intake_summary: Optional[str] = None) -> str:
    """Create a new tenant request (status 'new'); returns its id. The Triage Agent processes it separately."""
    customer_name, body = " ".join(customer_name.split()), body.strip()
    if not customer_name or not body:
        raise ValueError("customer name and message are required")
    if len(customer_name) > 128 or len(body) > MAX_BODY:
        raise ValueError(f"name max 128 chars, message max {MAX_BODY} chars")
    if account_tier not in ACCOUNT_TIERS:
        raise ValueError(f"account tier must be one of {ACCOUNT_TIERS}")
    if source not in ("csv", "chat"):
        raise ValueError("source must be 'csv' or 'chat'")
    # Thread lock covers one process; the primary key + retry covers several processes/replicas
    for _ in range(5):
        with _id_lock, SessionLocal() as s:
            nums = [int(m.group(1)) for (rid,) in s.execute(select(Request.request_id))
                    if (m := re.fullmatch(r"TR-(\d+)", rid))]
            request_id = f"TR-{max(nums, default=6000) + 1}"
            s.add(Request(request_id=request_id, customer_name=customer_name, account_tier=account_tier,
                          source_status="new", status=RequestStatus.NEW.value, submitted_at=utcnow(), body=body,
                          source=source, customer_user_id=customer_user_id, conversation_id=conversation_id,
                          intake_summary=intake_summary))
            try:
                s.commit()
                return request_id
            except IntegrityError:
                s.rollback()
    raise RuntimeError("could not allocate a request id")


def _require(reviewer: str, reason: str) -> None:
    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer and reason are required for every override")


def override_field(request_id: str, field: str, new_value: str, reviewer: str, reason: str) -> None:
    _require(reviewer, reason)
    if field not in EDITABLE or new_value not in EDITABLE[field]:
        raise ValueError(f"invalid override {field}={new_value}")
    with SessionLocal() as s:
        r = s.get(Request, request_id)
        old = getattr(r, field)
        if old == new_value:
            return
        setattr(r, field, new_value)
        r.human_reviewed = True
        s.add(Override(request_id=request_id, field=field, old_value=old, new_value=new_value, reviewer=reviewer, reason=reason))
        s.commit()


def confirm_triage(request_id: str, reviewer: str, note: str = "agent decision confirmed") -> None:
    _require(reviewer, note)
    with SessionLocal() as s:
        r = s.get(Request, request_id)
        r.human_reviewed = True
        s.add(Override(request_id=request_id, field="review", old_value=None, new_value="confirmed", reviewer=reviewer, reason=note))
        s.commit()


def reopen_duplicate(request_id: str, reviewer: str, reason: str) -> None:
    _require(reviewer, reason)
    with SessionLocal() as s:
        r = s.get(Request, request_id)
        if r.status != RequestStatus.CLOSED_DUPLICATE.value:
            raise ValueError("request is not closed as duplicate")
        for a in s.scalars(select(Action).where(Action.request_id == request_id, Action.action_type == "close_duplicate")):
            a.reverted = True
        s.add(Override(request_id=request_id, field="status", old_value=r.status, new_value=RequestStatus.ROUTED.value,
                       reviewer=reviewer, reason=f"reopened duplicate of {r.duplicate_of}: {reason}"))
        r.status, r.duplicate_of, r.human_reviewed = RequestStatus.ROUTED.value, None, True
        s.commit()


def _set_credit(credit_id: int, allowed_from: set[str], new_status: str, reviewer: str, reason: str) -> None:
    _require(reviewer, reason)
    with SessionLocal() as s:
        c = s.get(CourtesyCredit, credit_id)
        if c.status not in allowed_from:
            raise ValueError(f"credit is {c.status}; cannot change to {new_status}")
        s.add(Override(request_id=c.request_id, field="credit", old_value=f"{c.status} ${c.amount:.2f}",
                       new_value=f"{new_status} ${c.amount:.2f}", reviewer=reviewer, reason=reason))
        s.add(Action(request_id=c.request_id, action_type=f"credit_{new_status}", actor=reviewer,
                     details={"credit_id": c.id, "amount": c.amount, "reason": reason}))
        c.status, c.decided_by = new_status, reviewer
        r = s.get(Request, c.request_id)
        r.human_reviewed = True
        if new_status == "reversed" and r.status == RequestStatus.RESOLVED.value:
            r.status = RequestStatus.ROUTED.value
        s.commit()


def reverse_credit(credit_id: int, reviewer: str, reason: str) -> None:
    _set_credit(credit_id, {"issued"}, "reversed", reviewer, reason)


def approve_credit(credit_id: int, reviewer: str, reason: str) -> None:
    _set_credit(credit_id, {"pending_approval", "offered"}, "issued", reviewer, reason)


def reject_credit(credit_id: int, reviewer: str, reason: str) -> None:
    _set_credit(credit_id, {"pending_approval", "offered"}, "rejected", reviewer, reason)
