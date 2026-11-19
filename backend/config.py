"""Application settings for the Reliable AI Agent backend service."""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
try:
    from pydantic_settings import BaseSettings
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    from pydantic.v1 import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    """Strongly typed runtime configuration loaded from environment variables."""

    OPEN_SOURCE_API_KEY: str | None = None
    OPEN_SOURCE_BASE_URL: str = "https://router.huggingface.co/v1"
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    REDIS_URL: str = "redis://localhost:6379"
    PRIMARY_MODEL: str = "meta-llama/Llama-3.1-8B-Instruct"
    FALLBACK_MODEL: str = "Qwen/Qwen2.5-7B-Instruct"
    FALLBACK_MODEL_OPENAI: str | None = None
    FALLBACK_MODEL_ANTHROPIC: str | None = None
    VALIDATION_MODEL: str = "mistralai/Mistral-7B-Instruct-v0.3"
    # Ordered models for fallback, comma-separated. Empty means PRIMARY_MODEL, FALLBACK_MODEL,
    # VALIDATION_MODEL. Any OpenAI-compatible endpoint works via OPEN_SOURCE_BASE_URL (Hugging
    # Face router, Gemini's OpenAI-compatible API, Groq, a local Ollama, ...).
    MODEL_CHAIN: str = ""
    # Some endpoints reject an explicit temperature for certain models; set false to omit it.
    LLM_SEND_TEMPERATURE: bool = True
    # Optional reasoning effort for reasoning models (low|medium|high), sent as reasoning_effort.
    LLM_REASONING_EFFORT: str = ""
    MAX_RETRIES: int = 3
    STEP_TIMEOUT: int = 60
    MAX_STEPS: int = 15
    # Upper bound on planned steps; the planner is asked for the fewest steps that do the task.
    MAX_PLAN_STEPS: int = 6
    GRAPH_RECURSION_LIMIT: int = 0
    PARALLEL_MODE: bool = False
    MAX_CONCURRENT_STEPS: int = 5
    MULTI_AGENT_MODE: bool = True
    CHAOS_MODE: bool = False
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Security switches. Both tools act on the server's behalf, so they are off unless an
    # operator turns them on for a trusted, private deployment.
    # Per-task limits: the run stops and reports partial results when either is reached
    # (0 disables the limit).
    TASK_TOKEN_BUDGET: int = 60_000
    TASK_DEADLINE_SECONDS: int = 600
    # Multi-agent: "llm" lets the supervisor model pick the worker and write its brief;
    # "rules" uses the planner's tool hint and keywords (no extra model call).
    SUPERVISOR_MODE: str = "llm"
    MAX_AGENT_TOOL_CALLS: int = 3
    MAX_TOTAL_REFLECTIONS: int = 5
    # Ablation switch: when false a step that exhausts its retries is skipped without reflection.
    ENABLE_REFLECTION: bool = True
    # Ablation switch: when false, open circuits never skip a model.
    ENABLE_CIRCUIT_BREAKER: bool = True
    # Checkpointer for LangGraph state: "memory" (default), "sqlite:///path.db" or a
    # postgresql:// URL (for example a free Neon database) for resume across restarts.
    CHECKPOINT_URL: str = "memory"
    # Observability (see agent/telemetry.py). MODEL_PRICES is JSON:
    # {"model-name": [usd_per_1M_input, usd_per_1M_output]}; unpriced models report no cost.
    OTEL_SERVICE_NAME: str = "reliable-agent"
    OTEL_CONSOLE_EXPORT: bool = False
    MODEL_PRICES: str = ""
    ENABLE_CODE_EXEC: bool = False
    ENABLE_API_CALL: bool = False
    ADMIN_API_KEY: str | None = None

    class Config:
        """Pydantic configuration for environment-based settings loading."""

        case_sensitive = True

    @property
    def open_source_api_key(self) -> str:
        """Primary API key used for open-source model providers (for example OpenRouter)."""

        return (self.OPEN_SOURCE_API_KEY or "").strip()

    @property
    def open_source_base_url(self) -> str:
        """Base URL for OpenAI-compatible open-source model endpoints."""

        return (self.OPEN_SOURCE_BASE_URL or "").strip()

    @property
    def openai_api_key(self) -> str:
        """Backward-compatible OpenAI key alias with open-source key fallback."""

        if self.OPENAI_API_KEY:
            return self.OPENAI_API_KEY
        return self.open_source_api_key

    @property
    def anthropic_api_key(self) -> str:
        """Backward-compatible alias for lowercase Anthropic API key access."""

        return (self.ANTHROPIC_API_KEY or "").strip()

    @property
    def tavily_api_key(self) -> str:
        """Backward-compatible alias for lowercase Tavily API key access."""

        return (self.TAVILY_API_KEY or "").strip()

    @property
    def redis_url(self) -> str:
        """Backward-compatible alias for lowercase Redis URL access."""

        return self.REDIS_URL

    @property
    def model_chain(self) -> list[str]:
        """Ordered, de-duplicated model names used for fallback."""
        raw = [m.strip() for m in (self.MODEL_CHAIN or "").split(",") if m.strip()]
        if not raw:
            raw = [self.PRIMARY_MODEL, self.FALLBACK_MODEL, self.VALIDATION_MODEL]
        chain: list[str] = []
        for model in raw:
            if model and model not in chain:
                chain.append(model)
        return chain

    @property
    def primary_model(self) -> str:
        """Backward-compatible alias for lowercase primary model access."""

        return self.PRIMARY_MODEL

    @property
    def fallback_model_openai(self) -> str:
        """Backward-compatible alias for OpenAI fallback model access."""

        return self.FALLBACK_MODEL_OPENAI or self.FALLBACK_MODEL

    @property
    def fallback_model_anthropic(self) -> str:
        """Backward-compatible alias for Anthropic fallback model access."""

        return self.FALLBACK_MODEL_ANTHROPIC or self.FALLBACK_MODEL

    @property
    def graph_recursion_limit(self) -> int:
        """Optional explicit LangGraph recursion limit; 0 means auto-derive."""

        value = int(self.GRAPH_RECURSION_LIMIT or 0)
        if value <= 0:
            return 0
        return max(value, 25)

    @property
    def cors_origins(self) -> list[str]:
        """Comma-separated CORS origins normalized into a unique list."""

        raw_origins = (self.CORS_ORIGINS or "").strip()
        if not raw_origins:
            return ["http://localhost:5173", "http://127.0.0.1:5173"]

        origins: list[str] = []
        for origin in raw_origins.split(","):
            normalized = origin.strip().rstrip("/")
            if normalized and normalized not in origins:
                origins.append(normalized)

        if not origins:
            return ["http://localhost:5173", "http://127.0.0.1:5173"]

        return origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton settings instance for the current process."""

    return Settings()
