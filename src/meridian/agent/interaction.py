"""Customer Interaction Agent: the second agent, facing the customer.

Intake mode:  customer chat message -> understand the real need -> answer from the knowledge base, clarify,
              take a decision on an offered credit, add detail to an open ticket, or submit a new request.
Reply mode:   Triage Agent's structured outcome -> specific, helpful chat reply with next steps.

Same design as the Triage Agent: the LLM proposes, code decides. Identity (tenant name/tier) comes from the
logged-in account, the request body is the customer's own words (never LLM text), money moves only through
policy-checked code (a customer "yes" re-validates the offer against the billing record), and every outgoing
message passes output guardrails with a template fallback, so a customer always gets a safe answer.
"""
import json
import re
from typing import Optional

from sqlalchemy import select

from .. import chat, knowledge, services
from ..config import Settings, get_settings
from ..db import (Action, AgentRun, BillingRecord, Classification, CourtesyCredit, Request, SessionLocal, ToolCall,
                  utcnow)
from ..llm import LLMClient, get_llm
from ..observability import log
from ..policy.credit_guard import credit_reason
from .orchestrator import load_prompt

_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_PROMISE_RE = re.compile(r"\b(will|we'll|going to|have)\s+(be\s+)?(refund|credit|reimburs)", re.I)
MAX_INTAKE_STEPS = 5
MAX_REPLY_CHARS = 900
OPEN_STATUSES = ("new", "processing", "routed", "awaiting_customer", "needs_review", "acknowledged", "open")

# Service-level targets the business commits to per priority (configurable policy, not LLM output)
EXPECTED_RESPONSE = {"P1": "right away, it has been flagged as urgent", "P2": "later today",
                     "P3": "within 1-2 business days", "P4": "within 3 business days"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


INTAKE_TOOLS = [
    {"name": "search_knowledge_base", "strict": True,
     "description": "Search Meridian's customer knowledge base (gate hours, access contacts, fobs, insurance billing, "
                    "payments, late fees, transfers, move-out, climate control, pests). Use for general QUESTIONS. "
                    "Answer only from what it returns.",
     "input_schema": _obj({"query": {"type": "string"}})},
    {"name": "submit_service_request", "strict": True,
     "description": "Log the tenant's issue as a service request for the triage team. Only ONE ticket per "
                    "conversation: if this conversation already has a ticket, use add_note_to_request instead. "
                    "The request text is taken from the tenant's own messages automatically. Returns the ticket id.",
     "input_schema": _obj({"summary": {"type": "string", "description": "One neutral sentence: what the tenant needs, with specifics."},
                           "language": {"type": "string", "description": "ISO 639-1 code of the tenant's language."}})},
    {"name": "respond_to_credit_offer", "strict": True,
     "description": "Record the tenant's answer to an OPEN courtesy-credit offer (see context). accept=true applies "
                    "the verified credit; accept=false passes the ticket to the billing team.",
     "input_schema": _obj({"accept": {"type": "boolean"},
                           "reason": {"type": "string", "description": "The tenant's reason, if they gave one; else empty."}})},
    {"name": "add_note_to_request", "strict": True,
     "description": "Attach extra details from the tenant to one of THEIR open tickets (see context) instead of "
                    "opening a new one, e.g. photos, unit number, times.",
     "input_schema": _obj({"request_id": {"type": "string"}, "note": {"type": "string"}})},
    {"name": "get_ticket_status", "strict": True,
     "description": "Look up one of THIS tenant's tickets: status, team, what it is about, any credit, whether it "
                    "was merged, and the latest update already sent. Use whenever the tenant asks about a ticket or "
                    "for an update. Pass the ticket id they mentioned (e.g. TR-6069), or an empty string for this "
                    "conversation's ticket.",
     "input_schema": _obj({"request_id": {"type": "string"}})},
    {"name": "get_my_requests", "strict": True,
     "description": "List this tenant's tickets across all conversations (id, what it is about, status, team). "
                    "Use for 'what tickets do I have', 'which are still open', 'how many requests'. "
                    "only_open=true for unresolved/open/pending ones.",
     "input_schema": _obj({"only_open": {"type": "boolean"}})},
]
REPLY_TOOLS = [
    {"name": "send_reply", "strict": True, "description": "Send the chat message to the tenant.",
     "input_schema": _obj({"message": {"type": "string"}})},
]


# ------------------------------------------------------------------ guardrails
def _amounts(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _AMOUNT_RE.findall(text or "")}


def _money_ok(text: str, allowed: set[float]) -> bool:
    return all(any(abs(a - b) < 0.005 for b in allowed) for a in _amounts(text))


def guard_intake_reply(reply: str, allowed_amounts: set[float], request_id: Optional[str],
                       money_confirmed: bool = False) -> Optional[str]:
    """Returns the reply if safe, else None (caller uses a template). Amounts may only be ones the customer wrote,
    ones from knowledge-base sections returned this turn, or a verified offer/credit on their own account."""
    reply = (reply or "").strip()
    if not reply or len(reply) > MAX_REPLY_CHARS:
        return None
    if not money_confirmed and _PROMISE_RE.search(reply):
        return None
    if not _money_ok(reply, allowed_amounts):
        return None
    if request_id and request_id not in reply:
        reply += f" (Ticket {request_id})"
    return reply


def guard_outcome_reply(reply: str, outcome: dict) -> Optional[str]:
    reply = (reply or "").strip()
    credit = outcome.get("credit") or {}
    allowed = {credit["amount"]} if credit.get("status") in ("issued", "offered") else set()
    if outcome.get("claimed_amount"):
        allowed.add(outcome["claimed_amount"])            # the customer's own figure may be echoed back
    if not reply or len(reply) > MAX_REPLY_CHARS or not _money_ok(reply, allowed):
        return None
    if credit.get("status") != "issued" and _PROMISE_RE.search(reply):
        return None
    if outcome["request_id"] not in reply:
        reply += f" (Ticket {outcome['request_id']})"
    return reply


# ------------------------------------------------------------------ templates (fallbacks, and the offline agent)
def greeting(display_name: str) -> str:
    first = (display_name or "there").split()[0]
    return (f"Hi {first}! I'm Meridian's virtual assistant. Tell me what's going on, for example a billing "
            "question, a gate or lock problem, your unit, or damage, and I'll sort it out or get it to the right "
            "team. I can also answer general questions like gate hours.")


def template_ack(request_id: str) -> str:
    return (f"Thanks, I've logged this as ticket {request_id}. I'm checking it with our systems now "
            "and I'll post an update right here in a moment.")


def _with_next_steps(text: str, outcome: dict) -> str:
    steps = outcome.get("next_steps")
    return f"{text} {steps}" if steps else text


def template_reply(outcome: dict) -> str:
    rid, credit, reason = outcome["request_id"], outcome.get("credit") or {}, outcome.get("credit_reason")
    action, team = outcome["final_action"], outcome.get("team") or "care team"
    when = outcome.get("expected_response") or "soon"
    if action == "resolved_with_credit" and credit.get("status") == "issued":
        return (f"Good news: I checked your account and applied a courtesy credit of ${credit['amount']:.2f}. "
                f"Ticket {rid} is resolved. Is there anything else I can help with?")
    if credit.get("status") == "offered":
        claimed = outcome.get("claimed_amount")
        diff = f" rather than the ${claimed:.2f} mentioned" if claimed else ""
        return (f"I've checked your billing record for ticket {rid}. We can verify an overcharge of "
                f"${credit['amount']:.2f}{diff}. Would you like me to apply a ${credit['amount']:.2f} courtesy credit "
                "to your account now? Just reply yes or no. If you think the amount is wrong, say no and our billing "
                "team will go through your statement with you.")
    if credit.get("status") == "pending_approval":
        return (f"I've verified your billing concern on ticket {rid}. Because of the amount involved it needs a quick "
                "approval from our billing team, who will confirm with you directly.")
    if action == "closed_duplicate":
        return (f"This looks like a follow-up to your earlier ticket {outcome.get('duplicate_of')}, so I've added "
                f"your message to it to keep everything in one place (ticket {rid}).")
    if action == "needs_review":
        return f"I want to get this right, so a member of our team will review ticket {rid} personally and reply here."
    billing_reason = {
        "no_discrepancy_found": "I checked your billing record and couldn't find an overcharge on our side.",
        "balance_owed": "I checked your billing record and it shows an outstanding balance rather than an overcharge.",
        "needs_specific_amount": "To check a charge I need a bit more detail: which charge looks wrong, roughly how "
                                 "much, and on what date?",
        "compensation_request": "I understand this has been frustrating. I can't apply compensation myself, "
                                "but I've made sure our billing team sees your request.",
        "already_credited": "A courtesy credit was already applied to your account recently, so this one needs a "
                            "personal review.",
    }.get(reason)
    head = f"{billing_reason} " if billing_reason else ""
    urgent = " It has been flagged as urgent." if outcome.get("priority") == "P1" else ""
    return _with_next_steps(f"{head}Ticket {rid} is now with our {team}, and you can expect to hear from them "
                            f"{when}.{urgent}", outcome)


# ------------------------------------------------------------------ context helpers
def _open_offer(user_id: int, conversation_id: str) -> Optional[dict]:
    with SessionLocal() as s:
        row = s.execute(select(CourtesyCredit, Request)
                        .join(Request, Request.request_id == CourtesyCredit.request_id)
                        .where(CourtesyCredit.status == "offered", Request.customer_user_id == user_id,
                               Request.conversation_id == conversation_id)
                        .order_by(CourtesyCredit.id.desc())).first()
    if not row:
        return None
    c, r = row
    return {"credit_id": c.id, "request_id": r.request_id, "amount": c.amount, "claimed": c.claimed_amount}


def _open_tickets(user_id: int) -> list[dict]:
    return [r for r in chat.customer_requests(user_id, limit=5) if r["status"] in OPEN_STATUSES]


def _render_intake(user: dict, history: list[dict], offer: Optional[dict], tickets: list[dict],
                   conv_ticket: Optional[dict] = None, mentioned: Optional[dict] = None) -> str:
    lines = [f"Tenant: {user['display_name']} (account tier: {user.get('account_tier') or 'Standard'})"]
    for rid, found in (mentioned or {}).items():
        lines.append(f"The tenant's latest message refers to ticket {rid}: "
                     + ("it is theirs. Use get_ticket_status to answer; do NOT open a new ticket." if found else
                        "no such ticket on their account. Say you couldn't find it and ask them to check the number."))
    if conv_ticket:
        merged = conv_ticket.get("merged_into")
        lines.append(f"THIS CONVERSATION'S TICKET: {conv_ticket['request_id']} (status: {conv_ticket['status']}, "
                     f"team: {conv_ticket.get('team') or 'not assigned yet'}"
                     + (f", merged into {merged['request_id']} which is {merged['status']} with "
                        f"{merged.get('team') or 'the team'}" if merged else "") + "). "
                     "Do NOT open another ticket in this conversation: add new details to this one.")
    else:
        lines.append("This conversation has no ticket yet.")
    if offer:
        lines.append(f"OPEN OFFER on ticket {offer['request_id']}: verified courtesy credit of ${offer['amount']:.2f} "
                     "is waiting for the tenant's yes/no.")
    if tickets:
        lines.append("Tenant's open tickets: " + "; ".join(
            f"{t['request_id']} ({t['status']}): {t['summary'] or 'no summary'}" for t in tickets))
    lines.append("Conversation so far (oldest first):")
    for m in history:
        if m["sender"] == "customer":
            lines.append(f"<customer_message>\n{m['content']}\n</customer_message>")
        else:
            lines.append(f"Assistant: {m['content']}")
    last_bot = next((m["content"] for m in reversed(history) if m["sender"] == "assistant"), None)
    if last_bot:
        lines.append(f"Your previous message was: \"{last_bot}\". Short replies (yes / s / ok / no / thanks) "
                     "answer THAT message.")
    lines.append("Respond to the latest customer message.")
    return "\n".join(lines)


def _run_tools(llm: LLMClient, system: str, first_user: str, tools: list[dict], dispatch, max_steps: int) -> str:
    """Bounded tool loop shared by both modes. Returns the final text (may be empty)."""
    messages: list[dict] = [{"role": "user", "content": first_user}]
    final_text = ""
    for _ in range(max_steps):
        resp = llm.complete(system, messages, tools)
        if resp.stop_reason == "refusal":
            break
        messages.append({"role": "assistant", "content": resp.raw_content})
        if resp.text:
            final_text = resp.text
        if not resp.tool_uses:
            break
        results = []
        for tu in resp.tool_uses:
            out = dispatch(tu["name"], tu["input"] or {})
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(out, default=str),
                            **({"is_error": True} if "error" in out else {})})
            if "final_text" in out:
                return out["final_text"]
        messages.append({"role": "user", "content": results})
    return final_text


# ------------------------------------------------------------------ money: customer decision on an offer
_YES_RE = re.compile(r"^(yes|yeah|yep|yup|s|sure|ok|okay|please|go ahead|apply|y|correct|confirm|sounds good|si|sí)\b", re.I)
_QUESTION_RE = re.compile(r"\?|\b(status|update|what|why|how|when|which|where|who)\b", re.I)


def _is_clear_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(_YES_RE.match(t)) and len(t.split()) <= 8 and not _QUESTION_RE.search(t)



def resolve_offer(user: dict, conversation_id: str, accept: bool, reason: str = "",
                  settings: Optional[Settings] = None) -> dict:
    """Apply the tenant's yes/no. On yes the offer is RE-VALIDATED against the billing record before it is issued."""
    settings = settings or get_settings()
    offer = _open_offer(user["id"], conversation_id)
    if not offer:
        return {"error": "there is no open credit offer for this tenant in this conversation"}
    with SessionLocal() as s:
        credit, req = s.get(CourtesyCredit, offer["credit_id"]), s.get(Request, offer["request_id"])
        if not accept:
            credit.status, credit.decided_by = "declined", f"customer:{user['username']}"
            req.status = "routed"
            s.add(Action(request_id=req.request_id, action_type="credit_declined", actor=f"customer:{user['username']}",
                         details={"credit_id": credit.id, "offered": credit.amount, "customer_reason": reason[:500]}))
            s.commit()
            return {"declined": True, "request_id": req.request_id, "team": req.team}
        record = s.get(BillingRecord, req.customer_name)
        other = s.scalar(select(CourtesyCredit).where(CourtesyCredit.customer_name == req.customer_name,
                                                      CourtesyCredit.status == "issued"))
        problems = [msg for bad, msg in [
            (record is None or abs(record.verified_discrepancy - credit.amount) > 0.005, "billing record changed"),
            (credit.amount > settings.credit_auto_cap, "above auto-credit cap"),
            (other is not None, "a courtesy credit was already issued")] if bad]
        if problems:
            credit.status, req.status = "rejected", "routed"
            s.add(Action(request_id=req.request_id, action_type="credit_rejected", actor="policy",
                         details={"credit_id": credit.id, "reasons": problems}))
            s.commit()
            return {"error": "offer could not be applied: " + "; ".join(problems) + ". Tell the tenant the billing "
                             "team will review it.", "request_id": req.request_id}
        credit.status, credit.decided_by = "issued", f"customer-confirmed:{user['username']}"
        req.status = "resolved"
        s.add(Action(request_id=req.request_id, action_type="credit_issued", actor=f"customer:{user['username']}",
                     details={"credit_id": credit.id, "amount": credit.amount, "confirmed_at": utcnow().isoformat()},
                     tenant_message=f"Courtesy credit of ${credit.amount:.2f} applied."))
        s.commit()
        log.info("chat.offer_accepted", extra={"request_id": req.request_id, "amount": credit.amount})
        return {"accepted": True, "request_id": req.request_id, "amount": credit.amount}


# ------------------------------------------------------------------ intake mode
def handle_customer_message(user: dict, conversation_id: str, text: str, llm: Optional[LLMClient] = None,
                            settings: Optional[Settings] = None) -> dict:
    """Store the customer's message, let the agent respond, store and return the reply.

    `user` is the authenticated session's account (id, display_name, account_tier) - never chat input."""
    settings = settings or get_settings()
    text = (text or "").strip()
    if not text:
        raise ValueError("message is empty")
    if len(text) > settings.chat_max_chars:
        raise ValueError(f"message is too long (max {settings.chat_max_chars} characters)")
    chat.add_message(user["id"], conversation_id, "customer", text)          # raises if not the owner
    history = chat.get_messages(user["id"], conversation_id)[-settings.chat_history_messages:]
    offer, tickets = _open_offer(user["id"], conversation_id), _open_tickets(user["id"])
    conv_ticket = chat.conversation_ticket(user["id"], conversation_id)
    # Ticket numbers the customer typed, checked against THEIR account (other customers' tickets reveal nothing)
    mentioned = {rid: chat.ticket_status(user["id"], rid) is not None for rid in chat.mentioned_ticket_ids(text)}
    own_mentioned = [rid for rid, found in mentioned.items() if found]
    customer_text = " ".join(m["content"] for m in history if m["sender"] == "customer")
    state: dict = {"request_id": None, "allowed": _amounts(customer_text), "money_confirmed": False,
                   "linked_request": None, "looked_up": None, "listed": None}
    if offer:
        state["allowed"].add(offer["amount"])

    def submit(summary: str, language: str = "en") -> dict:
        if state["request_id"]:
            return {"error": "already submitted for this message", "request_id": state["request_id"]}
        if chat.asks_for_ticket_list(text):
            return {"error": "the tenant is asking to see their tickets, not reporting a new issue. Use "
                             "get_my_requests (only_open=true if they asked about unresolved ones)."}
        if own_mentioned:
            return {"error": f"the tenant is asking about existing ticket {own_mentioned[0]}. Use get_ticket_status "
                             "to answer, or add_note_to_request to add details. Do not open a new ticket."}
        if conv_ticket:
            return {"error": f"this conversation already has ticket {conv_ticket['request_id']}. Use "
                             "add_note_to_request to add these details to it, or answer the question. If this is a "
                             "completely different issue, ask the tenant to start a New conversation.",
                    "request_id": conv_ticket["request_id"]}
        if chat.requests_in_last_hour(user["id"]) >= settings.chat_max_requests_per_hour:
            return {"error": "rate limit: too many requests this hour; ask the tenant to wait or call the office"}
        body = chat.unsubmitted_customer_text(user["id"], conversation_id)
        if not body:
            return {"error": "no new customer text to submit"}
        rid = services.create_request(user["display_name"], user.get("account_tier") or "Standard", body,
                                      source="chat", customer_user_id=user["id"], conversation_id=conversation_id,
                                      intake_summary=(summary or "")[:300])
        state["request_id"] = rid
        log.info("chat.request_created", extra={"request_id": rid, "conversation_id": conversation_id})
        return {"submitted": True, "request_id": rid}

    def add_note(request_id: str, note: str) -> dict:
        own = ({t["request_id"] for t in tickets} | set(own_mentioned)
               | ({conv_ticket["request_id"]} if conv_ticket else set()))
        if request_id not in own:
            return {"error": "that ticket is not one of this tenant's open tickets"}
        with SessionLocal() as s:
            s.add(Action(request_id=request_id, action_type="follow_up_note", actor=f"customer:{user['username']}",
                         details={"note": note[:500], "customer_words": text[:1000]}))
            s.commit()
        state["linked_request"] = request_id
        return {"added": True, "request_id": request_id}

    def dispatch(name: str, args: dict) -> dict:
        if name == "search_knowledge_base":
            hits = knowledge.search(str(args.get("query", "")))
            for h in hits:
                state["allowed"] |= _amounts(h["content"])
            return {"results": hits} if hits else {"results": [], "note": "no answer in the knowledge base; submit a ticket"}
        if name == "submit_service_request":
            return submit(str(args.get("summary", "")), str(args.get("language", "en")))
        if name == "respond_to_credit_offer":
            accept = bool(args.get("accept"))
            if accept and not _is_clear_yes(text):   # money needs an explicit yes, never a question like "ok, status?"
                return {"error": "the tenant's message is not a clear yes. Answer their question and ask them to "
                                 "confirm the offer with yes or no."}
            out = resolve_offer(user, conversation_id, accept, str(args.get("reason", "")), settings)
            if out.get("accepted"):
                state["money_confirmed"] = True
                state["allowed"].add(out["amount"])
            if out.get("request_id"):
                state["linked_request"] = out["request_id"]
            return out
        if name == "add_note_to_request":
            return add_note(str(args.get("request_id", "")), str(args.get("note", "")))
        if name == "get_ticket_status":
            rid = str(args.get("request_id") or "").strip().upper() or \
                (own_mentioned[0] if own_mentioned else (conv_ticket or {}).get("request_id", ""))
            found = chat.ticket_status(user["id"], rid) if rid else None
            if found:
                state["linked_request"] = state["linked_request"] or found["request_id"]
                state["looked_up"] = found
                credit = found.get("credit") or {}
                if credit.get("status") in ("issued", "offered"):   # verified fact on their own account
                    state["allowed"].add(credit["amount"])
                # amounts the tenant themselves gave on that ticket, or that we already told them, may be repeated
                state["allowed"] |= _amounts(found.get("summary") or "") | _amounts(found.get("last_update_sent") or "")
                return {"ticket": found}
            return {"ticket": None, "note": f"no ticket {rid} on this tenant's account" if rid else
                    "no ticket in this conversation; ask which ticket they mean"}
        if name == "get_my_requests":
            reqs = chat.customer_requests(user["id"], only_open=bool(args.get("only_open")))
            for r in reqs:                          # their own summaries may be repeated back to them
                state["allowed"] |= _amounts(r.get("summary") or "")
            state["listed"] = reqs
            return {"requests": reqs, "count": len(reqs), "only_open": bool(args.get("only_open"))}
        return {"error": f"unknown tool {name}"}

    reply = None
    try:
        llm = llm or get_llm(settings.llm_provider)
        raw = _run_tools(llm, load_prompt(settings.interaction_prompt_version),
                         _render_intake(user, history, offer, tickets, conv_ticket, mentioned), INTAKE_TOOLS, dispatch,
                         MAX_INTAKE_STEPS)
        reply = guard_intake_reply(raw, state["allowed"], state["request_id"], state["money_confirmed"])
    except Exception:  # LLM/provider failure: never lose the customer's issue
        log.exception("interaction.intake_error", extra={"conversation_id": conversation_id})
        if (not state["request_id"] and not state["linked_request"] and len(text.split()) >= 3 and not own_mentioned
                and not chat.asks_for_ticket_list(text)):
            if conv_ticket:
                add_note(conv_ticket["request_id"], text[:200])
            else:
                submit(text[:150])
    if reply is None:
        if state["request_id"]:
            reply = template_ack(state["request_id"])
        elif state["listed"] is not None:
            reply = ("You don't have any tickets matching that." if not state["listed"] else
                     "Here are your tickets: " + "; ".join(f"{r['request_id']} ({r['status'].replace('_', ' ')})"
                                                           for r in state["listed"][:8]) + ".")
        elif state["looked_up"]:
            t = state["looked_up"]
            reply = (f"Ticket {t['request_id']} is currently {t['status'].replace('_', ' ')}"
                     + (f" with our {t['team']}" if t.get("team") else "") + ". I'll post any update right here.")
        elif state["linked_request"]:
            reply = f"Thanks, I've added that to ticket {state['linked_request']} so the team has it."
        else:
            reply = "Sorry, could you tell me a bit more so I can help?"
    link = state["request_id"] or state["linked_request"]
    chat.add_message(user["id"], conversation_id, "assistant", reply, request_id=link)
    return {"reply": reply, "request_id": state["request_id"], "linked_request": state["linked_request"]}


# ------------------------------------------------------------------ reply mode
def _credit_decision(request_id: str) -> tuple[Optional[str], list[str], Optional[float]]:
    """What the credit policy decided in the triage run, from the stored tool trace."""
    with SessionLocal() as s:
        calls = s.scalars(select(ToolCall).join(AgentRun, AgentRun.id == ToolCall.run_id)
                          .where(AgentRun.request_id == request_id, ToolCall.allowed.is_(True),
                                 ToolCall.tool_name.in_(["issue_courtesy_credit", "lookup_billing_record"]))
                          .order_by(ToolCall.id)).all()
    outcome, failed, record = None, [], None
    for c in calls:
        if c.tool_name == "lookup_billing_record" and c.result.get("found"):
            record = c.result.get("verified_discrepancy")
        if c.tool_name == "issue_courtesy_credit":
            outcome = c.result.get("outcome")
            failed = [ch["rule"] for ch in c.result.get("checks", []) if not ch["passed"]]
    return outcome, failed, record


def build_outcome(request_id: str) -> dict:
    """The Triage Agent's result, reduced to the facts the customer may be told."""
    with SessionLocal() as s:
        r = s.get(Request, request_id)
        credit = s.scalar(select(CourtesyCredit).where(CourtesyCredit.request_id == request_id))
        cls = s.scalar(select(Classification).where(Classification.request_id == request_id)
                       .order_by(Classification.id.desc()))
        msg = s.scalar(select(Action.tenant_message).where(Action.request_id == request_id,
                                                           Action.tenant_message.is_not(None))
                       .order_by(Action.id.desc()))
    if r.status == "closed_duplicate":
        action = "closed_duplicate"
    elif r.status == "needs_review":
        action = "needs_review"
    elif credit and credit.status == "issued" and r.status == "resolved":
        action = "resolved_with_credit"
    elif r.status == "awaiting_customer":
        action = "awaiting_customer_decision"
    else:
        action = "routed"
    decided, failed, record = _credit_decision(request_id)
    return {"request_id": request_id, "final_action": action, "category": r.category, "priority": r.priority,
            "team": r.team, "expected_response": EXPECTED_RESPONSE.get(r.priority or ""),
            "duplicate_of": r.duplicate_of, "language": cls.language if cls else "en",
            "claimed_amount": cls.claimed_amount if cls else None,
            "credit": {"amount": credit.amount, "status": credit.status} if credit else None,
            "credit_reason": credit_reason(decided, failed, record) if decided else None,
            "next_steps": knowledge.next_steps(r.category) if action == "routed" else None,
            "triage_message_to_tenant": msg}


def compose_reply(request_id: str, llm: Optional[LLMClient] = None, settings: Optional[Settings] = None) -> str:
    settings = settings or get_settings()
    outcome = build_outcome(request_id)
    try:
        llm = llm or get_llm(settings.llm_provider)
        captured: dict = {}

        def dispatch(name: str, args: dict) -> dict:
            if name == "send_reply":
                captured["message"] = str(args.get("message", ""))
                return {"sent": True, "final_text": captured["message"]}
            return {"error": f"unknown tool {name}"}

        raw = _run_tools(llm, load_prompt("interaction_reply_v1"),
                         f"<outcome>\n{json.dumps(outcome, default=str)}\n</outcome>", REPLY_TOOLS, dispatch, 2)
        safe = guard_outcome_reply(raw, outcome)
        if safe:
            return safe
        log.info("interaction.reply_guarded", extra={"request_id": request_id})
    except Exception:
        log.exception("interaction.reply_error", extra={"request_id": request_id})
    return template_reply(outcome)
