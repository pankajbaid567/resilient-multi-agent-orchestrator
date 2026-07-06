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
    TAVILY_API_KEY: str
    REDIS_URL: str = "redis://localhost:6379"
    PRIMARY_MODEL: str = "meta-llama/Llama-3.1-8B-Instruct"
    FALLBACK_MODEL: str = "Qwen/Qwen2.5-7B-Instruct"
    FALLBACK_MODEL_OPENAI: str | None = None
    FALLBACK_MODEL_ANTHROPIC: str | None = None
    VALIDATION_MODEL: str = "mistralai/Mistral-7B-Instruct-v0.3"
    MAX_RETRIES: int = 3
    STEP_TIMEOUT: int = 60
    MAX_STEPS: int = 15
    GRAPH_RECURSION_LIMIT: int = 0
    PARALLEL_MODE: bool = False
    MAX_CONCURRENT_STEPS: int = 5
    MULTI_AGENT_MODE: bool = True
    CHAOS_MODE: bool = False
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

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

        return self.TAVILY_API_KEY

    @property
    def redis_url(self) -> str:
        """Backward-compatible alias for lowercase Redis URL access."""

        return self.REDIS_URL

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
