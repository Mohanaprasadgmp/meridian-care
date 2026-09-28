# Original path: tests/test_openai_adapter.py
"""OpenAI adapter: full agent loop against a scripted fake Chat Completions server (no network, no key)."""
import json
from types import SimpleNamespace as NS

from sqlalchemy import select

from meridian.agent.orchestrator import triage_request
from meridian.db import CourtesyCredit, Request, SessionLocal
from meridian.llm.openai_client import OpenAILLM, to_openai_tools
from meridian.tools.definitions import TOOLS

SCRIPT = [
    ("record_classification", {"category": "Billing & Autopay Dispute", "confidence": 0.93,
                               "urgency_signals": ["billing_discrepancy_claimed"], "language": "en",
                               "claim_kind": "overcharge", "claimed_amount": 22.40, "claim_is_specific": True,
                               "summary": "Autopay $22.40 over usual rent", "reasoning": "explicit amount on own autopay"}),
    ("search_request_history", {"rationale": "check duplicates"}),
    ("lookup_billing_record", {"rationale": "verify claim"}),
    ("issue_courtesy_credit", {"rationale": "record confirms 22.40"}),
    ("acknowledge_request", {"tenant_message": "We applied a courtesy credit of $22.40 to your account."}),
]


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        msgs = kw["messages"]
        # Contract checks on what the adapter sends
        assert msgs[0]["role"] == "system"
        issued = {tc["id"] for m in msgs if m["role"] == "assistant" for tc in m.get("tool_calls", [])}
        answered = [m["tool_call_id"] for m in msgs if m["role"] == "tool"]
        assert set(answered) == issued, "every tool_call must be answered by a tool message"
        step = len(self.calls) - 1
        usage = NS(prompt_tokens=100, completion_tokens=20)
        if step < len(SCRIPT):
            name, args = SCRIPT[step]
            tc = NS(id=f"call_{step}", function=NS(name=name, arguments=json.dumps(args)))
            msg = NS(content=None, tool_calls=[tc], refusal=None)
            return NS(choices=[NS(message=msg, finish_reason="tool_calls")], usage=usage, model="gpt-4.1-2025-04-14")
        msg = NS(content="Credited $22.40 and acknowledged.", tool_calls=None, refusal=None)
        return NS(choices=[NS(message=msg, finish_reason="stop")], usage=usage, model="gpt-4.1-2025-04-14")


def make_llm(compat=False, context_tokens=None):
    from meridian.config import get_settings
    llm = OpenAILLM(model="gpt-4.1", base_url="http://gateway.test/v1", api_key="test-key", settings=get_settings(),
                    compat=compat, context_tokens=context_tokens)
    llm.client = NS(chat=NS(completions=FakeCompletions()))
    return llm


def test_openai_mode_sends_strict_and_sequential_tool_calls():
    kw = make_llm().request_kwargs("sys", [{"role": "user", "content": "hi"}], TOOLS)
    assert kw["parallel_tool_calls"] is False and "max_completion_tokens" in kw
    assert all(t["function"]["strict"] for t in kw["tools"])


def test_company_compat_mode_sends_only_portable_params():
    kw = make_llm(compat=True).request_kwargs("sys", [{"role": "user", "content": "hi"}], TOOLS)
    assert "max_tokens" in kw and "max_completion_tokens" not in kw and "parallel_tool_calls" not in kw
    assert all("strict" not in t["function"] for t in kw["tools"])


def test_company_agent_loop_end_to_end_in_compat_mode(seeded):
    out = triage_request("TR-6037", llm=make_llm(compat=True, context_tokens=131_000))
    assert out["status"] == "completed" and out["request_status"] == "resolved"


def test_context_budget_exceeded_falls_back_to_human(seeded):
    out = triage_request("TR-6037", llm=make_llm(compat=True, context_tokens=500))
    assert out["status"] == "fallback" and out["team"] == "Human Triage"


def test_company_provider_requires_key(monkeypatch):
    import pytest
    from meridian import config
    from meridian.llm import get_llm
    monkeypatch.setenv("MERIDIAN_COMPANY_API_KEY", "")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="MERIDIAN_COMPANY_API_KEY"):
        get_llm("company")
    config.get_settings.cache_clear()


def test_company_client_targets_gateway(monkeypatch):
    from meridian import config
    monkeypatch.setenv("MERIDIAN_COMPANY_API_KEY", "abc")
    config.get_settings.cache_clear()
    llm = OpenAILLM.for_company()
    assert str(llm.client.base_url).rstrip("/") == "https://api.llm.cib.echonet/v1/openai"
    assert llm.client.api_key == "abc" and llm.model == "gpt-oss-120b-ITG" and llm.compat
    config.get_settings.cache_clear()


def test_tool_schema_conversion():
    t = to_openai_tools(TOOLS)
    assert len(t) == len(TOOLS) and all(x["type"] == "function" and x["function"]["strict"] for x in t)
    assert t[0]["function"]["parameters"]["additionalProperties"] is False


def test_openai_agent_loop_end_to_end(seeded):
    llm = make_llm()
    out = triage_request("TR-6037", llm=llm)
    assert out["status"] == "completed" and out["request_status"] == "resolved"
    assert out["cost_usd"] > 0                                # dated snapshot name still priced
    with SessionLocal() as s:
        c = s.scalar(select(CourtesyCredit).where(CourtesyCredit.request_id == "TR-6037"))
        assert c.status == "issued" and c.amount == 22.40
        assert s.get(Request, "TR-6037").team == "Billing Team"


def test_invalid_json_arguments_are_rejected_not_crashing(seeded):
    llm = make_llm()
    fc = llm.client.chat.completions
    orig = fc.create

    def bad_first(**kw):
        if not fc.calls:
            fc.calls.append(kw)
            tc = NS(id="call_bad", function=NS(name="record_classification", arguments="{not json"))
            return NS(choices=[NS(message=NS(content=None, tool_calls=[tc], refusal=None), finish_reason="tool_calls")],
                      usage=NS(prompt_tokens=1, completion_tokens=1), model="gpt-4.1")
        fc.calls.append(kw)
        return NS(choices=[NS(message=NS(content="give up", tool_calls=None, refusal=None), finish_reason="stop")],
                  usage=NS(prompt_tokens=1, completion_tokens=1), model="gpt-4.1")

    fc.create = bad_first
    out = triage_request("TR-6037", llm=llm)
    assert out["status"] == "fallback" and out["team"] == "Human Triage"
    fc.create = orig
