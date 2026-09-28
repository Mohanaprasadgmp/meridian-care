# Original path: src/meridian/chat.py
"""Chat storage with ownership checks.

Routing rule: every message lives in a conversation, every conversation belongs to exactly one customer
account, and every read/write here verifies that ownership. Replies therefore reach only the customer who
raised the request, regardless of how many customers are chatting at once or which server/worker produced
the reply. The browser session is never trusted as identity.
"""
import re
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select

from .db import ChatMessage, Conversation, CourtesyCredit, Request, SessionLocal, utcnow


class NotYourConversation(PermissionError):
    pass


def _owned(s, customer_user_id: int, conversation_id: str) -> Conversation:
    conv = s.get(Conversation, conversation_id)
    if conv is None or conv.customer_user_id != customer_user_id:
        raise NotYourConversation("conversation not found")   # same error either way: no existence oracle
    return conv


def start_conversation(customer_user_id: int, greeting: Optional[str] = None) -> str:
    with SessionLocal() as s:
        conv = Conversation(id=str(uuid.uuid4()), customer_user_id=customer_user_id)
        s.add(conv)
        s.flush()   # conversation row must exist before the greeting that references it
        if greeting:
            s.add(ChatMessage(conversation_id=conv.id, sender="assistant", content=greeting))
        s.commit()
        return conv.id


def list_conversations(customer_user_id: int) -> list[dict]:
    with SessionLocal() as s:
        rows = s.execute(
            select(Conversation.id, Conversation.created_at, Conversation.last_activity_at,
                   func.count(Request.request_id))
            .outerjoin(Request, Request.conversation_id == Conversation.id)
            .where(Conversation.customer_user_id == customer_user_id, Conversation.deleted_at.is_(None))
            .group_by(Conversation.id).order_by(Conversation.last_activity_at.desc())).all()
    return [{"id": r[0], "created_at": r[1], "last_activity_at": r[2], "tickets": r[3]} for r in rows]


def get_messages(customer_user_id: int, conversation_id: str) -> list[dict]:
    with SessionLocal() as s:
        _owned(s, customer_user_id, conversation_id)
        msgs = s.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
                         .order_by(ChatMessage.id)).all()
        status = {r.request_id: r.status for r in s.scalars(
            select(Request).where(Request.conversation_id == conversation_id))}
    return [{"id": m.id, "sender": m.sender, "content": m.content, "request_id": m.request_id,
             "request_status": status.get(m.request_id), "created_at": m.created_at} for m in msgs]


def add_message(customer_user_id: int, conversation_id: str, sender: str, content: str,
                request_id: Optional[str] = None) -> int:
    with SessionLocal() as s:
        conv = _owned(s, customer_user_id, conversation_id)
        if conv.deleted_at is not None:
            raise NotYourConversation("conversation not found")
        m = ChatMessage(conversation_id=conversation_id, sender=sender, content=content, request_id=request_id)
        s.add(m)
        conv.last_activity_at = utcnow()
        s.commit()
        return m.id


def delete_conversation(customer_user_id: int, conversation_id: str) -> None:
    """Hides the conversation from the customer. Its tickets keep being worked on and still show up in
    'what tickets do I have'; the admin console keeps the full transcript for audit."""
    with SessionLocal() as s:
        conv = _owned(s, customer_user_id, conversation_id)
        conv.deleted_at = conv.deleted_at or utcnow()
        s.commit()


def deliver_system_reply(conversation_id: str, content: str, request_id: str) -> int:
    """Used by the worker: writes into the conversation the request was raised in (ownership is implied by
    the request row, which was created inside that customer's own conversation)."""
    with SessionLocal() as s:
        conv = s.get(Conversation, conversation_id)
        req = s.get(Request, request_id)
        if conv is None or req is None or req.conversation_id != conversation_id \
                or req.customer_user_id != conv.customer_user_id:
            raise NotYourConversation("request/conversation mismatch")
        m = ChatMessage(conversation_id=conversation_id, sender="assistant", content=content, request_id=request_id)
        s.add(m)
        conv.last_activity_at = utcnow()
        s.commit()
        return m.id


CLOSED_STATUSES = ("resolved", "closed", "closed_duplicate")
_TICKET_WORD = re.compile(r"\b(tick\w*|requests?|cases?|complaints?)\b", re.I)
_LIST_ASK = re.compile(r"\b(what|which|show|list|how many|see|view|tell me|give me|all|any open|still open)\b", re.I)
_RAISE = re.compile(r"\b(raise|report|new|log|file|submit|create|make a)\b", re.I)


def asks_for_ticket_list(text: str) -> bool:
    """'what are all the tickets I have', 'show my open requests', 'waht are all the ticket i have' ... but NOT
    'I want to raise a complaint' (a new issue) and NOT a question about one specific ticket number."""
    t = text or ""
    return bool(_TICKET_WORD.search(t) and _LIST_ASK.search(t)) and not _RAISE.search(t) and not TICKET_RE.search(t)


def customer_requests(customer_user_id: int, limit: int = 10, only_open: bool = False) -> list[dict]:
    with SessionLocal() as s:
        q = select(Request).where(Request.customer_user_id == customer_user_id)
        if only_open:
            q = q.where(Request.status.not_in(CLOSED_STATUSES))
        rows = s.scalars(q.order_by(Request.submitted_at.desc()).limit(limit)).all()
    return [{"request_id": r.request_id, "status": r.status, "team": r.team, "category": r.category,
             "priority": r.priority, "submitted_at": r.submitted_at.isoformat(),
             "summary": r.intake_summary or (r.body[:90] + ("…" if len(r.body) > 90 else "")),
             "duplicate_of": r.duplicate_of} for r in rows]


def requests_in_last_hour(customer_user_id: int) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Request).where(
            Request.customer_user_id == customer_user_id, Request.submitted_at >= utcnow() - timedelta(hours=1)))


def unsubmitted_customer_text(customer_user_id: int, conversation_id: str) -> str:
    """The customer's own words since the last ticket in this conversation. The request body is built from
    these verbatim, never from the LLM, so the Triage Agent sees exactly what the customer said."""
    msgs = get_messages(customer_user_id, conversation_id)
    last_ticket = max((i for i, m in enumerate(msgs) if m["request_id"]), default=-1)
    return "\n".join(m["content"] for m in msgs[last_ticket + 1:] if m["sender"] == "customer").strip()


TICKET_RE = re.compile(r"\bTR-?\s?(\d{3,})\b", re.I)


def mentioned_ticket_ids(text: str) -> list[str]:
    """Ticket numbers the customer typed, normalised: 'tr 6069', 'TR6069', 'TR-6069' -> 'TR-6069'."""
    return list(dict.fromkeys(f"TR-{n}" for n in TICKET_RE.findall(text or "")))


def ticket_status(customer_user_id: int, request_id: str) -> Optional[dict]:
    """Customer-safe view of ONE of this customer's tickets. Returns None if it doesn't exist OR isn't theirs
    (same answer either way, so ticket numbers of other customers reveal nothing)."""
    with SessionLocal() as s:
        r = s.get(Request, (request_id or "").upper())
        if r is None or r.customer_user_id != customer_user_id:
            return None
        merged = s.get(Request, r.duplicate_of) if r.duplicate_of else None
        credit = s.scalar(select(CourtesyCredit).where(CourtesyCredit.request_id == r.request_id))
        last_update = s.scalar(select(ChatMessage.content).where(
            ChatMessage.sender == "assistant", ChatMessage.request_id == r.request_id).order_by(ChatMessage.id.desc()))
    return {"request_id": r.request_id, "status": r.status, "team": r.team, "category": r.category,
            "summary": r.intake_summary, "submitted_at": r.submitted_at.isoformat(),
            "merged_into": ({"request_id": merged.request_id, "status": merged.status, "team": merged.team}
                            if merged else None),
            "credit": {"amount": credit.amount, "status": credit.status} if credit else None,
            "last_update_sent": last_update}


def conversation_ticket(customer_user_id: int, conversation_id: str) -> Optional[dict]:
    """The single ticket that belongs to this conversation (one conversation = one ticket)."""
    with SessionLocal() as s:
        _owned(s, customer_user_id, conversation_id)
        rid = s.scalar(select(Request.request_id).where(Request.conversation_id == conversation_id)
                       .order_by(Request.submitted_at).limit(1))
    return ticket_status(customer_user_id, rid) if rid else None
