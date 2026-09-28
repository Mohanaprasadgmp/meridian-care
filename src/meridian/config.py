"""Central configuration. Every threshold that affects money or routing lives here, not in prompts."""
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
# Export .env to the process so provider SDKs find ANTHROPIC_API_KEY / OPENAI_API_KEY (never overrides real env vars)
load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="MERIDIAN_", extra="ignore")

    # LLM
    llm_provider: str = "company"           # "company" | "openai" | "claude" | "fake" (deterministic offline agent)
    model: str = "claude-opus-5"
    openai_model: str = "gpt-4.1"
    openai_base_url: str = ""               # blank = api.openai.com; set for any OpenAI-compatible endpoint

    # Company LLM gateway (LLM@CIB Developer Assistant: OpenAI-compatible API)
    company_base_url: str = "https://api.llm.cib.echonet/v1/openai"
    company_api_key: str = ""               # personal key from the Developer Assistant page; never commit it
    company_model: str = "gpt-oss-120b-ITG"
    company_context_tokens: int = 131_000   # the model's REAL context size (gpt-oss-120b-ITG: 131K)
    company_ca_bundle: str = ""             # path to the corporate root CA .pem if TLS verification fails
    # Self-hosted open models behind OpenAI-compatible gateways often reject OpenAI-only parameters
    # (strict schemas, parallel_tool_calls, max_completion_tokens). Compat mode sends only the portable subset.
    company_compat_mode: bool = True
    effort: str = "medium"                  # triage is short-horizon; medium holds quality at lower cost
    max_tokens: int = 4000
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 8               # SDK backoff honours retry-after; low-tier keys hit TPM limits
    use_refusal_fallback: bool = True       # server-side fallback on policy declines

    # Agent loop bounds
    max_agent_steps: int = 8
    workers: int = 4

    # Customer chat + Customer Interaction Agent
    interaction_prompt_version: str = "interaction_v1"
    chat_max_chars: int = 2000               # per customer message
    chat_max_requests_per_hour: int = 5      # per customer: stops ticket flooding
    chat_history_messages: int = 12          # context window the Interaction Agent sees

    # Background triage worker (processes chat requests; CSV stays manual via CLI / console button)
    embedded_worker: bool = True             # run one worker thread inside the Streamlit process
    worker_poll_s: float = 2.0
    worker_threads: int = 2
    worker_sources: str = "chat"             # comma list: "chat" or "chat,csv"
    processing_timeout_s: int = 600          # a request stuck in "processing" this long is re-queued

    # Policy thresholds
    context_confidence_threshold: float = 0.75   # below -> must pull customer context
    human_triage_threshold: float = 0.55         # final confidence below -> Human Triage, no autonomy
    credit_min_confidence: float = 0.80
    credit_auto_cap: float = 50.00               # above -> pending human approval
    credit_abs_tolerance: float = 2.00
    credit_rel_tolerance: float = 0.10

    # Storage
    database_url: str = f"sqlite:///{(ROOT / 'meridian.db').as_posix()}"
    data_dir: Path = ROOT / "data"
    prompt_version: str = "triage_v1"

    @property
    def active_model(self) -> str:
        return {"claude": self.model, "openai": self.openai_model,
                "company": self.company_model}.get(self.llm_provider, "offline keyword agent")


@lru_cache
def get_settings() -> Settings:
    return Settings()
