from .base import LLMClient, LLMResponse


def get_llm(provider: str) -> LLMClient:
    if provider == "fake":
        from .fake import FakeLLM
        return FakeLLM()
    if provider == "claude":
        from .claude import ClaudeLLM
        return ClaudeLLM()
    if provider == "openai":
        from .openai_client import OpenAILLM
        return OpenAILLM.for_openai()
    if provider == "company":
        from .openai_client import OpenAILLM
        return OpenAILLM.for_company()
    raise ValueError(f"unknown llm provider '{provider}'")


__all__ = ["LLMClient", "LLMResponse", "get_llm"]
