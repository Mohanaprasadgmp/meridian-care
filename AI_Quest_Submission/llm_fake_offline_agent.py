# Original path: src/meridian/llm/fake.py
"""Deterministic keyword agent that speaks the same tool protocol as the real LLM.

Uses: offline demo without an API key, CI tests of the agent loop and guardrails, and a
rules baseline that the eval compares the LLM against.
"""
import json
import re
from typing import Any

from .base import LLMResponse

KW = {
    "Damage & Insurance Claim": ["water", "leak", "drip", "sprinkler", "mold", "crack", "tear", "damage", "pest",
                                 "dust", "debris", "spotting", "ruined", "insurance coverage", "coverage limits", "coverage summary"],
    "Delinquency & Auction Notice": ["lien", "auction", "late payment", "grace period", "payment plan", "good standing",
                                     "account standing", "payment is a few days late"],
    "Billing & Autopay Dispute": ["charge", "charged", "autopay", "statement", "invoice", "refund", "deposit", "credit",
                                  "balance", "payment history", "discount", "fee", "cobró", "billed", "reimbursement",
                                  "rate sheet", "overcharge", "shorted"],
    "Gate Access & Lockout": ["gate", "fob", "code", "lock", "keypad", "padlock", "hasp", "access"],
    "Unit Transfer & Reservation Change": ["swap", "transfer", "reservation", "larger", "smaller", "downsize", "dolly",
                                           "second unit", "move-out", "move from", "loading dock", "lease"],
}
PRIORITY_ORDER = ["Damage & Insurance Claim", "Delinquency & Auction Notice", "Billing & Autopay Dispute",
                  "Gate Access & Lockout", "Unit Transfer & Reservation Change"]
AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def heuristic_classify(body: str) -> dict:
    t = body.lower()
    amount = AMOUNT_RE.search(body)
    hits = {c: sum(w in t for w in KW[c]) for c in KW}
    money = amount is not None or _has(t, ["charged", "cobró"])
    if money and not _has(t, ["water", "mold", "sprinkler"]):
        category, conf = "Billing & Autopay Dispute", 0.9
    elif max(hits.values()) == 0:
        category, conf = "Unit Transfer & Reservation Change", 0.5
    else:
        top = max(hits.values())
        category = next(c for c in PRIORITY_ORDER if hits[c] == top)
        conf = 0.85 if top >= 2 or sum(v > 0 for v in hits.values()) == 1 else 0.7

    signals = []
    if _has(t, ["mold", "fire", "flood"]):
        signals.append("safety_risk")
    if _has(t, ["pooling", "dripping", "ruined", "actively"]):
        signals.append("active_property_damage")
    elif category == "Damage & Insurance Claim" and _has(t, ["crack", "tear", "dust", "spotting"]):
        signals.append("minor_damage_reported")
    if _has(t, ["stopped working", "isn't opening", "locked out", "jamming", "won't open"]):
        signals.append("locked_out")
    if _has(t, ["tomorrow", "in about an hour", "before i leave", "before then"]):
        signals.append("time_critical_deadline")
    if _has(t, ["again", "still not fixed", "still isn't"]):
        signals.append("repeat_unresolved_issue")
    if _has(t, ["auto-drafts", "auto-draft"]):
        signals.append("financial_impact_imminent")
    if _has(t, ["no rush", "whenever convenient"]):
        signals.append("explicitly_not_urgent")

    claim_kind, specific, claimed = "none", False, None
    if category == "Billing & Autopay Dispute" and money:
        claimed = float(amount.group(1)) if amount else None
        if _has(t, ["make this right", "make it right"]):
            claim_kind = "goodwill_or_compensation_request"
        elif _has(t, ["twice", "double", "dos veces"]):
            claim_kind = "duplicate_charge"
        elif _has(t, ["short", "owed", "less than", "missing", "shorted", "credit"]):
            claim_kind = "missing_credit_or_refund"
        else:
            claim_kind = "overcharge"
        specific = claimed is not None and not _has(t, ["not sure by how much", "couple", "someone in our building", "as well?"])
        signals.append("billing_discrepancy_claimed")
    if not signals or (not money and not set(signals) - {"explicitly_not_urgent"} and
                       re.match(r"^(can|could|what|is|requesting|would|wondering|how)", t)):
        signals.append("informational_only")

    lang = "es" if _has(t, ["hola", "quería", "gracias"]) else "en"
    return {"category": category, "confidence": conf, "urgency_signals": list(dict.fromkeys(signals)),
            "language": lang, "claim_kind": claim_kind, "claimed_amount": claimed, "claim_is_specific": specific,
            "summary": body[:120], "reasoning": f"keyword baseline: matched {hits}"}


class FakeLLM:
    model = "fake-keyword-agent"

    def __init__(self) -> None:
        self._n = 0

    def _call(self, name: str, args: dict[str, Any]) -> LLMResponse:
        self._n += 1
        block = {"type": "tool_use", "id": f"toolu_fake_{self._n}", "name": name, "input": args}
        return LLMResponse(raw_content=[block], tool_uses=[{"id": block["id"], "name": name, "input": args}],
                           text="", stop_reason="tool_use", model=self.model)

    def _text(self, text: str) -> LLMResponse:
        return LLMResponse(raw_content=[{"type": "text", "text": text}], tool_uses=[], text=text,
                           stop_reason="end_turn", model=self.model)

    @staticmethod
    def _tool_history(messages: list[dict]) -> dict[str, dict]:
        calls, results = [], {}
        for m in messages[1:]:
            for b in m["content"] if isinstance(m["content"], list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls.append((b["id"], b["name"]))
                elif isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = json.loads(b["content"])
        return {name: results.get(cid, {}) for cid, name in calls}

    _STATUS_TEXT = {"new": "received and queued", "processing": "being reviewed right now",
                    "routed": "being handled", "awaiting_customer": "waiting for your reply",
                    "resolved": "resolved", "closed_duplicate": "merged into an earlier ticket",
                    "needs_review": "with a specialist for a personal review", "acknowledged": "acknowledged"}
    _YES = r"^(yes|yeah|yep|yup|s|sure|ok|okay|please|go ahead|apply( it)?|y|correct|right)\b"
    _NO = r"^(no|nope|nah|n|not really|that's wrong|that is wrong|incorrect)\b"
    _CLOSING = r"^(thanks|thank you|thx|ty|that's all|thats all|nothing else|no thanks|no thank you|bye|ok thanks|okay thanks|great thanks|cool)\b"

    def _ticket_line(self, ticket: tuple | None) -> str:
        if not ticket:
            return ""
        rid, status = ticket
        return f" Your ticket {rid} is {self._STATUS_TEXT.get(status, status)}."

    @staticmethod
    def _refusal(error: str) -> str:
        if "rate limit" in error:
            return ("You've raised several tickets in the last hour, so I can't open another one just now. I've "
                    "noted your message. Please try again a bit later, or call the office if it's urgent.")
        if "already has ticket" in error:
            return "This conversation already has a ticket, so I've kept everything together there."
        return "I wasn't able to open a ticket for that. Could you tell me a bit more about what you need?"

    def _intake(self, messages: list[dict]) -> LLMResponse:
        """Customer Interaction Agent, intake mode (deterministic, context-aware): offer decision / closing /
        status / follow-up note / KB answer / clarify / submit. One ticket per conversation."""
        ctx = messages[0]["content"]
        msgs = re.findall(r"<customer_message>\n?(.*?)\n?</customer_message>", ctx, re.S)
        last = (msgs[-1] if msgs else "").strip()
        low, done = last.lower().strip(), self._tool_history(messages)
        offer = re.search(r"OPEN OFFER on ticket (TR-\d+)", ctx)
        m = re.search(r"THIS CONVERSATION'S TICKET: (TR-\d+) \(status: ([a-z_]+)", ctx)
        ticket = (m.group(1), m.group(2)) if m else None
        lb = re.search(r'Your previous message was: "(.*?)"\. Short replies', ctx, re.S)
        last_bot = (lb.group(1) if lb else "").lower()
        open_ids = re.findall(r"(TR-\d+) \((?:new|processing|routed|awaiting_customer|needs_review|acknowledged|open)\)", ctx)

        # ---- after a tool result: write the reply
        if "respond_to_credit_offer" in done:
            res = done["respond_to_credit_offer"]
            if res.get("accepted"):
                return self._text(f"Done! I've applied a ${res['amount']:.2f} courtesy credit to your account. "
                                  f"Ticket {res['request_id']} is resolved. Anything else I can help with?")
            if res.get("declined"):
                return self._text(f"No problem. I've passed ticket {res['request_id']} to our billing team, who will "
                                  "go through your statement with you.")
            return self._text("I couldn't apply that automatically, so our billing team will review it with you.")
        if "add_note_to_request" in done:
            res = done["add_note_to_request"]
            return self._text(f"Thanks, I've added that to ticket {res.get('request_id')} so the team has everything. "
                              "If this is a separate issue, tap New conversation and I'll open a new ticket for it.")
        if "submit_service_request" in done:
            res = done["submit_service_request"]
            return self._text(f"Thanks, I've logged this as ticket {res['request_id']}. I'm checking it now and the "
                              "result will appear here shortly."
                              if res.get("request_id") and "error" not in res else self._refusal(res.get("error", "")))
        if "search_knowledge_base" in done:
            hits = done["search_knowledge_base"].get("results", [])
            if hits:
                first = " ".join(re.split(r"(?<=[.!?])\s+", hits[0]["content"])[:3])
                return self._text(f"{first} Is there anything else I can help with?")
            if ticket:
                return self._call("add_note_to_request", {"request_id": ticket[0], "note": last[:200]})
            return self._call("submit_service_request", {"summary": last[:150], "language": "en"})
        if "get_ticket_status" in done:
            res = done["get_ticket_status"]
            t = res.get("ticket")
            if not t:
                missing = re.search(r"TR-\d+", res.get("note", ""))
                return self._text(f"I couldn't find ticket {missing.group(0)} on your account. Could you double-check "
                                  "the number?" if missing else "There's no ticket in this conversation yet. Which "
                                  "ticket do you mean, or what can I help you with?")
            text = f"Here's the latest on ticket {t['request_id']}"
            if t.get("summary"):
                text += f", about \"{t['summary'].rstrip('.')}\""
            text += f": it is {self._STATUS_TEXT.get(t['status'], t['status'])}"
            if t.get("team") and t["status"] not in ("resolved", "closed_duplicate"):
                text += f" with our {t['team']}"
            if t.get("merged_into"):
                mt = t["merged_into"]
                text += (f". It was merged into your earlier ticket {mt['request_id']}, which is "
                         f"{self._STATUS_TEXT.get(mt['status'], mt['status'])}"
                         + (f" with our {mt['team']}" if mt.get("team") else ""))
            credit = t.get("credit") or {}
            if credit.get("status") == "issued":
                text += f". A courtesy credit of ${credit['amount']:.2f} was applied"
            elif credit.get("status") == "offered":
                text += f". There's an offer of a ${credit['amount']:.2f} courtesy credit waiting for your yes or no"
            return self._text(text + ". I'll post any new update right here. Anything else I can help with?")
        if "get_my_requests" in done:
            res = done["get_my_requests"]
            reqs = res.get("requests", [])
            if not reqs:
                return self._text("You don't have any open tickets right now." if res.get("only_open") else
                                  "You don't have any tickets yet. What can I help you with?")
            head = (f"You have {len(reqs)} open ticket{'s' if len(reqs) != 1 else ''}:" if res.get("only_open")
                    else f"Here are your {len(reqs)} most recent tickets:")
            lines = []
            for r in reqs[:6]:
                where = self._STATUS_TEXT.get(r["status"], r["status"])
                if r.get("team") and r["status"] not in ("resolved", "closed_duplicate", "needs_review"):
                    where += f" with our {r['team']}"
                if r.get("duplicate_of"):
                    where = f"merged into {r['duplicate_of']}"
                about = (r.get("summary") or "").rstrip(".")
                about = about if len(about) <= 55 else about[:52].rstrip() + "..."
                lines.append(f"{r['request_id']}: {about}, {where}")
            if len(reqs) > 6:
                lines.append(f"...and {len(reqs) - 6} more.")
            return self._text(head + "\n" + "\n".join(lines) + "\nWould you like details on any of these?")

        # ---- decide, using the conversation context
        if re.fullmatch(r"(hi|hello|hey|hola)[!. ]*", low):
            return self._text("Hi! What can I help you with today?" + self._ticket_line(ticket))
        ref = re.search(r"\bTR-?\s?(\d{3,})\b", last, re.I)
        if ref:                                   # the customer is asking about a specific ticket: look it up
            return self._call("get_ticket_status", {"request_id": f"TR-{ref.group(1)}"})
        from ..chat import asks_for_ticket_list      # lazy: avoids an import cycle
        if asks_for_ticket_list(last):
            only_open = bool(re.search(r"\b(open|pending|unresolved|not resolved|outstanding|still)\b", low))
            return self._call("get_my_requests", {"only_open": only_open})
        asks_status = bool(re.search(r"\b(status|update|updates|progress|any news|what happened|latest)\b", low))
        # A short answer, not a question: "ok what's the latest update?" must never count as accepting money
        short_answer = len(low.split()) <= 4 and "?" not in low and not asks_status
        if asks_status:
            if ticket:
                return self._call("get_ticket_status", {"request_id": ""})
            return self._call("get_my_requests", {"only_open": True})
        if offer and short_answer and re.match(self._YES, low):
            return self._call("respond_to_credit_offer", {"accept": True, "reason": ""})
        if offer and re.match(self._NO, low):
            return self._call("respond_to_credit_offer", {"accept": False, "reason": last[:200]})
        if re.match(self._CLOSING, low) or (re.match(self._NO, low) and "anything else" in last_bot):
            return self._text("You're welcome! " + self._ticket_line(ticket).strip() +
                              " You can come back to this chat any time for an update.")
        if short_answer and re.match(self._YES, low):
            if "anything else" in last_bot:
                return self._text("Sure, what else can I help you with?")
            return self._text("Great." + self._ticket_line(ticket) + " Is there anything else I can help with?")
        if re.match(self._NO, low) and len(low.split()) <= 3:
            return self._text("No problem." + self._ticket_line(ticket) + " Let me know if you need anything else.")
        vague = re.fullmatch(r"(i )?(have|got|need) (a |an |some )?(problem|issue|question|help)[!. ]*|help[!. ]*", low)
        if ticket and not vague and len(last.split()) < 4:
            return self._call("add_note_to_request", {"request_id": ticket[0], "note": last[:200]})
        if vague or len(last.split()) < 4:
            return self._text("Could you tell me a bit more about the issue, for example what happened and when?")
        if not ticket and "$" not in last and any(k in low for k in (
                "not sure by how much", "seems off", "looks off", "is wrong", "a couple dollars",
                "make this right", "make it right")):
            return self._text("I can check that for you. Which charge looks wrong, roughly how much was it, "
                              "and on what date?")
        problem = any(k in low for k in ("stopped", "broken", "leak", "water", "charged", "overcharged", "jammed",
                                         "locked", "damage", "flood", "won't", "cannot", "can't get", "mold"))
        question = low.endswith("?") or re.match(r"^(what|when|how|can|do|does|is|are|where|which|who|could)\b", low)
        if question and not problem:
            return self._call("search_knowledge_base", {"query": last})
        if ticket or (open_ids and low.startswith(("also", "here are", "here is", "it is unit", "it's unit"))):
            return self._call("add_note_to_request", {"request_id": ticket[0] if ticket else open_ids[0],
                                                      "note": last[:200]})
        lang = "es" if any(w in low for w in ("hola", "quería", "gracias", "cobró")) else "en"
        return self._call("submit_service_request", {"summary": last[:150], "language": lang})

    def _reply(self, messages: list[dict]) -> LLMResponse:
        """Customer Interaction Agent, reply mode: turn the triage outcome into a message."""
        if self._tool_history(messages):
            return self._text("sent")
        from ..agent.interaction import template_reply   # lazy: avoids an import cycle
        outcome = json.loads(re.search(r"<outcome>\n?(.*?)\n?</outcome>", messages[0]["content"], re.S).group(1))
        return self._call("send_reply", {"message": template_reply(outcome)})

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        names = {t["name"] for t in tools}
        if "submit_service_request" in names:
            return self._intake(messages)
        if "send_reply" in names:
            return self._reply(messages)
        body = re.search(r"<tenant_request>\n?(.*?)\n?</tenant_request>", messages[0]["content"], re.S).group(1)
        req_id = re.search(r"request_id: (\S+)", messages[0]["content"]).group(1)
        calls, results = [], {}
        for m in messages[1:]:
            for b in m["content"] if isinstance(m["content"], list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls.append((b["id"], b["name"]))
                elif isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = json.loads(b["content"])
        done = {name: results.get(cid, {}) for cid, name in calls}
        last_error = calls and "error" in results.get(calls[-1][0], {})

        if any(n in done and "error" not in done[n] for n in ("route_request", "acknowledge_request", "close_as_duplicate")):
            return LLMResponse(raw_content=[{"type": "text", "text": "Done."}], tool_uses=[], text="Done.",
                               stop_reason="end_turn", model=self.model)
        cls = heuristic_classify(body)
        ack = ("Gracias por contactarnos. Hemos recibido su solicitud y nuestro equipo la revisará pronto."
               if cls["language"] == "es" else
               "Thanks for contacting Meridian Self Storage. We've received your request and the right team is on it.")
        if "record_classification" not in done:
            return self._call("record_classification", cls)
        if "search_request_history" not in done:
            return self._call("search_request_history", {"rationale": "check duplicates/related history"})
        if cls["confidence"] < 0.75 and "get_customer_context" not in done:
            return self._call("get_customer_context", {"rationale": "low confidence"})
        billing = cls["category"] == "Billing & Autopay Dispute"
        if billing and cls["claim_is_specific"] and "lookup_billing_record" not in done:
            return self._call("lookup_billing_record", {"rationale": "verify claimed discrepancy"})
        rec = done.get("lookup_billing_record", {})
        if billing and rec.get("found") and rec.get("verified_discrepancy", 0) > 0 and "issue_courtesy_credit" not in done:
            return self._call("issue_courtesy_credit", {"rationale": "record appears to confirm the claim"})
        credit = done.get("issue_courtesy_credit", {})
        if credit.get("outcome") == "issue" and "acknowledge_request" not in done:
            return self._call("acknowledge_request", {"tenant_message":
                f"We reviewed your account and applied a courtesy credit of ${credit['amount']:.2f}. Thank you for letting us know."})
        hist = done.get("search_request_history", {}).get("other_requests", [])
        dup = next((h for h in hist if h["submitted_before_this"] and h["status"] in ("new", "open", "routed")
                    and h["text_similarity"] >= 0.2 and h.get("category") in (None, cls["category"])), None)
        if dup and "close_as_duplicate" not in done and not last_error:
            return self._call("close_as_duplicate", {"duplicate_of": dup["request_id"], "tenant_message": ack,
                                                     "reason": f"same tenant, similar earlier open request {dup['request_id']}"})
        note = f"{req_id}: {cls['category']}; checks: {', '.join(done)}"
        if credit:
            note += f"; credit outcome={credit.get('outcome')}"
        return self._call("route_request", {"tenant_message": ack, "internal_note": note})
