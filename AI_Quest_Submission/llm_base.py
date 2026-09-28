# Original path: src/meridian/llm/base.py
"""Provider-neutral LLM interface so the in-house model can be swapped by configuration."""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    raw_content: Any                 # appended verbatim to the conversation (keeps provider-specific blocks intact)
    tool_uses: list[dict]            # [{"id", "name", "input"}]
    text: str
    stop_reason: str                 # end_turn | tool_use | max_tokens | refusal | ...
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    meta: dict = field(default_factory=dict)


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
