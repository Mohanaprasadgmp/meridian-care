"""Agent protocol enforcement, output guardrails, failure handling, and a full offline run."""
from sqlalchemy import select

from meridian.agent.orchestrator import triage_all, triage_request
from meridian.config import get_settings
from meridian.db import Action, AgentRun, CourtesyCredit, Request, SessionLocal, ToolCall
from meridian.llm.base import LLMResponse
from meridian.tools.environment import ToolEnvironment

CLS = {"category": "Billing & Autopay Dispute", "confidence": 0.9, "urgency_signals": ["billing_discrepancy_claimed"],
       "language": "en", "claim_kind": "overcharge", "claimed_amount": 22.40, "claim_is_specific": True,
       "summary": "s", "reasoning": "r"}
MSG = "Thanks, we're looking into it."


def env_for(request_id):
    s = SessionLocal()
    run = AgentRun(request_id=request_id, model="t", prompt_version="t", status="running")
    s.add(run)
    s.flush()
    return s, ToolEnvironment(session=s, request=s.get(Request, request_id), run_id=run.id, settings=get_settings())


def blocked(res):
    result, allowed = res
    return not allowed and "error" in result


# ---- protocol enforcement ----
def test_final_action_requires_classification_and_history(seeded):
    s, env = env_for("TR-6037")
    assert blocked(env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""}))
    env.dispatch("record_classification", CLS)
    r, ok = env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""})
    assert not ok and "search_request_history" in r["error"]


def test_low_confidence_requires_context_tool(seeded):
    s, env = env_for("TR-6014")
    env.dispatch("record_classification", {**CLS, "category": "Unit Transfer & Reservation Change", "confidence": 0.5,
                                           "claim_kind": "none", "claimed_amount": None, "claim_is_specific": False})
    env.dispatch("search_request_history", {"rationale": ""})
    r, ok = env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""})
    assert not ok and "get_customer_context" in r["error"]
    env.dispatch("get_customer_context", {"rationale": ""})
    assert env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""})[1]


def test_specific_billing_claim_requires_record_lookup(seeded):
    s, env = env_for("TR-6037")
    env.dispatch("record_classification", CLS)
    env.dispatch("search_request_history", {"rationale": ""})
    r, ok = env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""})
    assert not ok and "lookup_billing_record" in r["error"]


def test_billing_lookup_forbidden_for_non_billing(seeded):
    s, env = env_for("TR-6051")
    env.dispatch("record_classification", {**CLS, "category": "Gate Access & Lockout", "claim_kind": "none",
                                           "claimed_amount": None, "claim_is_specific": False})
    assert blocked(env.dispatch("lookup_billing_record", {"rationale": ""}))


def test_acknowledge_without_credit_blocked(seeded):
    s, env = env_for("TR-6051")
    env.dispatch("record_classification", {**CLS, "category": "Gate Access & Lockout", "claim_kind": "none",
                                           "claimed_amount": None, "claim_is_specific": False})
    env.dispatch("search_request_history", {"rationale": ""})
    assert blocked(env.dispatch("acknowledge_request", {"tenant_message": MSG}))


def test_only_one_final_action(seeded):
    s, env = env_for("TR-6051")
    env.dispatch("record_classification", {**CLS, "category": "Gate Access & Lockout", "claim_kind": "none",
                                           "claimed_amount": None, "claim_is_specific": False})
    env.dispatch("search_request_history", {"rationale": ""})
    assert env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""})[1]
    assert blocked(env.dispatch("route_request", {"tenant_message": MSG, "internal_note": ""}))


# ---- money + output guardrails ----
def test_credit_amount_comes_from_record_and_message_cannot_invent_money(seeded):
    s, env = env_for("TR-6013")                                  # Ravi claims $22; record 22.75
    env.dispatch("record_classification", {**CLS, "claimed_amount": 22.0})
    env.dispatch("search_request_history", {"rationale": ""})
    env.dispatch("lookup_billing_record", {"rationale": ""})
    res, ok = env.dispatch("issue_courtesy_credit", {"rationale": "", "amount": 999})  # extra arg ignored by design
    assert ok and res["outcome"] == "issue" and res["amount"] == 22.75
    assert blocked(env.dispatch("acknowledge_request", {"tenant_message": "We credited $22.00."}))
    assert env.dispatch("acknowledge_request", {"tenant_message": "We applied a courtesy credit of $22.75."})[1]


def test_route_message_cannot_promise_refund(seeded):
    s, env = env_for("TR-6005")                                  # Delphine: record 0.00
    env.dispatch("record_classification", {**CLS, "claimed_amount": 8.0, "claim_kind": "missing_credit_or_refund"})
    env.dispatch("search_request_history", {"rationale": ""})
    env.dispatch("lookup_billing_record", {"rationale": ""})
    assert env.dispatch("issue_courtesy_credit", {"rationale": ""})[0]["outcome"] == "deny"
    assert blocked(env.dispatch("route_request", {"tenant_message": "We will refund you shortly.", "internal_note": ""}))
    assert blocked(env.dispatch("route_request", {"tenant_message": "Your $8 is on the way.", "internal_note": ""}))


def test_duplicate_must_be_same_tenant(seeded):
    s, env = env_for("TR-6041")                                  # same text as TR-6032, different tenant
    env.dispatch("record_classification", {**CLS, "category": "Unit Transfer & Reservation Change", "claim_kind": "none",
                                           "claimed_amount": None, "claim_is_specific": False})
    env.dispatch("search_request_history", {"rationale": ""})
    r, ok = env.dispatch("close_as_duplicate", {"duplicate_of": "TR-6032", "tenant_message": MSG, "reason": "same text"})
    assert not ok and "same tenant" in r["error"]


def test_closed_ticket_is_not_a_duplicate_target(seeded):
    s, env = env_for("TR-6038")                                  # TR-6088 is closed/resolved history
    env.dispatch("record_classification", {**CLS, "category": "Gate Access & Lockout", "claim_kind": "none",
                                           "claimed_amount": None, "claim_is_specific": False})
    env.dispatch("search_request_history", {"rationale": ""})
    r, ok = env.dispatch("close_as_duplicate", {"duplicate_of": "TR-6088", "tenant_message": MSG, "reason": ""})
    assert not ok and "closed" in r["error"]


# ---- orchestrator failure handling ----
class ExplodingLLM:
    model = "boom"

    def complete(self, *a):
        raise TimeoutError("provider timeout")


class LoopingLLM:
    model = "loop"
    n = 0

    def complete(self, *a):
        self.n += 1
        tid = f"t{self.n}"
        return LLMResponse(raw_content=[{"type": "tool_use", "id": tid, "name": "search_request_history", "input": {"rationale": ""}}],
                           tool_uses=[{"id": tid, "name": "search_request_history", "input": {"rationale": ""}}],
                           text="", stop_reason="tool_use")


class RefusingLLM:
    model = "refuse"

    def complete(self, *a):
        return LLMResponse(raw_content=[], tool_uses=[], text="", stop_reason="refusal")


def _assert_fallback(rid):
    with SessionLocal() as s:
        r = s.get(Request, rid)
        assert r.team == "Human Triage" and r.status == "needs_review"
        assert s.scalar(select(Action).where(Action.request_id == rid, Action.action_type == "fallback_route"))


def test_provider_error_falls_back_to_human(seeded):
    assert triage_request("TR-6037", llm=ExplodingLLM())["status"] == "fallback"
    _assert_fallback("TR-6037")


def test_step_budget_falls_back_to_human(seeded):
    out = triage_request("TR-6037", llm=LoopingLLM())
    assert out["status"] == "fallback" and out["steps"] == get_settings().max_agent_steps
    _assert_fallback("TR-6037")


def test_provider_error_can_be_requeued_but_model_fallbacks_cannot(seeded):
    from meridian.agent.orchestrator import pending_request_ids, requeue_failed
    triage_request("TR-6037", llm=ExplodingLLM())           # provider error -> retryable
    triage_request("TR-6013", llm=RefusingLLM())            # model behaviour -> stays with humans
    assert requeue_failed() == ["TR-6037"]
    assert "TR-6037" in pending_request_ids() and "TR-6013" not in pending_request_ids()
    with SessionLocal() as s:
        assert s.get(Request, "TR-6037").team is None


def test_refusal_falls_back_to_human(seeded):
    assert triage_request("TR-6037", llm=RefusingLLM())["status"] == "fallback"


# ---- end-to-end on the seed data (offline agent) ----
def test_full_run_offline(seeded):
    res = triage_all(workers=1)
    assert len(res) == 99                                        # TR-6088 already closed -> skipped
    with SessionLocal() as s:
        credits = {c.request_id: c for c in s.scalars(select(CourtesyCredit))}
        assert "TR-6073" not in credits                          # goodwill demand never paid
        assert credits["TR-6070"].status == "pending_approval"   # above cap -> human
        assert all(c.amount <= get_settings().credit_auto_cap for c in credits.values() if c.status == "issued")
        assert s.get(Request, "TR-6039").duplicate_of == "TR-6038"
        same_text = ["TR-6032", "TR-6041", "TR-6075", "TR-6077"]
        assert all(s.get(Request, i).status != "closed_duplicate" for i in same_text)
        assert s.get(Request, "TR-6096").team == "Facilities Emergency Response"
        assert s.scalar(select(ToolCall).where(ToolCall.tool_name == "record_classification")) is not None
