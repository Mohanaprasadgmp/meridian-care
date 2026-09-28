# Original path: src/meridian/llm/claude.py
"""Claude implementation of the LLM interface (Anthropic Python SDK)."""
import anthropic

from ..config import get_settings
from .base import LLMResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeLLM:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.model = s.model
        # SDK retries 408/409/429/5xx and connection errors with exponential backoff
        self.client = anthropic.Anthropic(timeout=s.llm_timeout_s, max_retries=s.llm_max_retries)

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        s = self.settings
        kwargs = dict(
            model=self.model,
            max_tokens=s.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": s.effort},
            cache_control={"type": "ephemeral"},   # system + tools are identical across all requests
        )
        if s.use_refusal_fallback:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        resp = self.client.beta.messages.create(**kwargs)
        tool_uses = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        u = resp.usage
        return LLMResponse(
            raw_content=resp.content, tool_uses=tool_uses, text=text, stop_reason=resp.stop_reason or "",
            input_tokens=(u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                         + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            output_tokens=u.output_tokens or 0, model=resp.model,
            meta={"cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0},
        )
