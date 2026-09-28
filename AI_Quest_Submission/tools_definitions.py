# Original path: src/meridian/tools/definitions.py
"""Tool schemas exposed to the LLM. Strict schemas: the model cannot send fields we don't expect.

Least privilege: data tools take no tenant identifiers - they are bound to the request under
triage, so text inside a tenant message cannot steer the agent into reading another account.
Money tools take no amount - the amount always comes from the billing record.
"""
from ..models import Category, ClaimKind, Signal


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": list(props) if required is None else required, "additionalProperties": False}


TENANT_MSG = {"type": "string", "description": "Short, polite acknowledgment sent to the tenant (max 600 chars). "
              "Never promise refunds, credits or amounts unless a credit was actually issued by issue_courtesy_credit."}

TOOLS: list[dict] = [
    {
        "name": "record_classification",
        "description": "Record your classification of the tenant request. Call this first. You may call it again "
                       "after gathering more context to revise it. Returns the policy-computed priority and team "
                       "plus which steps are still required before you can act.",
        "strict": True,
        "input_schema": _obj({
            "category": {"type": "string", "enum": [c.value for c in Category]},
            "confidence": {"type": "number", "description": "0.0-1.0. Use <0.75 when the request does not clearly fit one category."},
            "urgency_signals": {"type": "array", "items": {"type": "string", "enum": [s.value for s in Signal]},
                                "description": "Only signals actually supported by the text. Capital letters or 'URGENT' alone are not a signal; a real deadline or harm is."},
            "language": {"type": "string", "description": "ISO 639-1 code of the tenant's message."},
            "claim_kind": {"type": "string", "enum": [k.value for k in ClaimKind]},
            "claimed_amount": {"type": ["number", "null"], "description": "Dollar amount the tenant says is wrong, or null if none/unclear."},
            "claim_is_specific": {"type": "boolean", "description": "True only if the tenant states a concrete, checkable dollar discrepancy about their own account (hearsay, 'not sure by how much', 'a couple dollars' are NOT specific)."},
            "summary": {"type": "string", "description": "One-sentence neutral summary for staff."},
            "reasoning": {"type": "string", "description": "Why this category, confidence and these signals. Shown to human reviewers."},
        }),
    },
    {
        "name": "search_request_history",
        "description": "Find this tenant's other requests (exact customer-name match) to detect duplicates, repeats "
                       "or related history. Required before any final action.",
        "strict": True,
        "input_schema": _obj({"rationale": {"type": "string"}}),
    },
    {
        "name": "get_customer_context",
        "description": "Pull account context (tier, request counts, open tickets, billing-record presence, prior credits). "
                       "Required when your classification confidence is below 0.75; use it to firm up or revise the classification.",
        "strict": True,
        "input_schema": _obj({"rationale": {"type": "string"}}),
    },
    {
        "name": "lookup_billing_record",
        "description": "Fetch the verified billing discrepancy for this tenant. Only for Billing & Autopay Dispute requests. "
                       "Required before acting on a billing request that claims a specific amount.",
        "strict": True,
        "input_schema": _obj({"rationale": {"type": "string"}}),
    },
    {
        "name": "issue_courtesy_credit",
        "description": "Attempt a courtesy credit for a verified billing discrepancy. The amount is taken from the billing "
                       "record by policy (you cannot set it). Returns issue / pending_approval / deny with the rule results. "
                       "After an issued credit, call acknowledge_request; otherwise call route_request.",
        "strict": True,
        "input_schema": _obj({"rationale": {"type": "string"}}),
    },
    {
        "name": "acknowledge_request",
        "description": "FINAL ACTION. Acknowledge and resolve the request. Only valid after a courtesy credit was issued for it.",
        "strict": True,
        "input_schema": _obj({"tenant_message": TENANT_MSG}),
    },
    {
        "name": "route_request",
        "description": "FINAL ACTION. Acknowledge the tenant and route the request to the policy-selected team.",
        "strict": True,
        "input_schema": _obj({"tenant_message": TENANT_MSG,
                              "internal_note": {"type": "string", "description": "Handoff note for the team: what was checked and what is needed."}}),
    },
    {
        "name": "close_as_duplicate",
        "description": "FINAL ACTION. Close this request as a genuine duplicate of an earlier OPEN/NEW request from the SAME "
                       "tenant about the SAME issue. The original gets a follow-up note. Never for different tenants.",
        "strict": True,
        "input_schema": _obj({"duplicate_of": {"type": "string", "description": "request_id of the earlier request"},
                              "tenant_message": TENANT_MSG,
                              "reason": {"type": "string"}}),
    },
]

TERMINAL_TOOLS = {"acknowledge_request", "route_request", "close_as_duplicate"}
