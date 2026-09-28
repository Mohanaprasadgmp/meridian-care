# Original path: src/meridian/llm/openai_client.py
"""OpenAI-compatible implementation of the LLM interface.

Serves both OpenAI itself and the company LLM gateway (LLM@CIB, OpenAI-compatible). The orchestrator
keeps a provider-neutral conversation (user text, assistant turns, tool_result blocks); this adapter
translates it to Chat Completions messages and back, so the agent loop, tools and guardrails are
identical across providers.
"""
import json
from typing import Optional

from openai import DefaultHttpxClient, OpenAI

from ..config import Settings, get_settings
from .base import LLMResponse

STOP_MAP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class ContextBudgetError(RuntimeError):
    """The request would not fit the model's real context window; the orchestrator falls back to humans."""


def to_openai_tools(tools: list[dict], strict: bool = True) -> list[dict]:
    out = []
    for t in tools:
        fn = {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
        if strict:
            fn["strict"] = t.get("strict", False)
        out.append({"type": "function", "function": fn})
    return out


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant":
            out.append(content)                      # already an OpenAI assistant message (see raw_content below)
        elif isinstance(content, list):              # tool results -> one "tool" message per call
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
        else:
            out.append({"role": "user", "content": content})
    return out


def estimate_tokens(payload) -> int:
    # Conservative (~3.5 chars/token); only used to stay inside the context window, not for billing
    return int(len(json.dumps(payload, default=str)) / 3.5) + 1


class OpenAILLM:
    def __init__(self, *, model: str, base_url: Optional[str], api_key: Optional[str], settings: Settings,
                 compat: bool = False, context_tokens: Optional[int] = None, ca_bundle: str = "") -> None:
        self.settings = settings
        self.model = model
        self.compat = compat
        self.context_tokens = context_tokens
        # Corporate gateways often sit behind an internal CA: pass its .pem so TLS verification still happens
        http_client = DefaultHttpxClient(verify=ca_bundle) if ca_bundle else None
        self.client = OpenAI(base_url=base_url or None, api_key=api_key or None, timeout=settings.llm_timeout_s,
                             max_retries=settings.llm_max_retries, http_client=http_client)

    @classmethod
    def for_openai(cls) -> "OpenAILLM":
        s = get_settings()   # api_key None -> the SDK reads OPENAI_API_KEY
        return cls(model=s.openai_model, base_url=s.openai_base_url, api_key=None, settings=s)

    @classmethod
    def for_company(cls) -> "OpenAILLM":
        s = get_settings()
        if not s.company_api_key:
            raise RuntimeError("MERIDIAN_COMPANY_API_KEY is not set in .env")
        return cls(model=s.company_model, base_url=s.company_base_url, api_key=s.company_api_key, settings=s,
                   compat=s.company_compat_mode, context_tokens=s.company_context_tokens, ca_bundle=s.company_ca_bundle)

    def request_kwargs(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        kw = dict(model=self.model, messages=to_openai_messages(system, messages),
                  tools=to_openai_tools(tools, strict=not self.compat))
        if self.compat:
            kw["max_tokens"] = self.settings.max_tokens              # portable across vLLM / LiteLLM gateways
        else:
            kw["parallel_tool_calls"] = False                        # one step at a time keeps the order explicit
            kw["max_completion_tokens"] = self.settings.max_tokens
        if self.context_tokens:
            needed = estimate_tokens(kw["messages"]) + estimate_tokens(kw["tools"]) + self.settings.max_tokens
            if needed > self.context_tokens:
                raise ContextBudgetError(f"request needs ~{needed} tokens > context {self.context_tokens}")
        return kw

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        resp = self.client.chat.completions.create(**self.request_kwargs(system, messages, tools))
        choice = resp.choices[0]
        msg = choice.message
        tool_uses = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__invalid_json__": tc.function.arguments}   # dispatch rejects it; model can retry
            tool_uses.append({"id": tc.id, "name": tc.function.name, "input": args})
        raw = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            raw["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                 for tc in msg.tool_calls]
        stop = "refusal" if getattr(msg, "refusal", None) else STOP_MAP.get(choice.finish_reason, choice.finish_reason or "")
        u = resp.usage
        return LLMResponse(raw_content=raw, tool_uses=tool_uses, text=(msg.content or "").strip(), stop_reason=stop,
                           input_tokens=(u.prompt_tokens or 0) if u else 0,
                           output_tokens=(u.completion_tokens or 0) if u else 0, model=resp.model or self.model)
