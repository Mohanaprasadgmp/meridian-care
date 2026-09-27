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

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
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
