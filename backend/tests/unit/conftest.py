"""Deterministic test setup: no network, no Redis, no real model keys.

Environment variables are pinned before any app module is imported, so the developer's
local backend/.env (which load_dotenv would otherwise read) can never leak real keys into
tests. Every LLM call goes through the FakeLLM installed by the ``fake_llm`` fixture.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PINNED_ENV = {
    "OPEN_SOURCE_API_KEY": "test-key",
    "OPEN_SOURCE_BASE_URL": "http://fake-llm.invalid/v1",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "TAVILY_API_KEY": "",
    "ADMIN_API_KEY": "",
    "REDIS_URL": "redis://127.0.0.1:1",
    "ENABLE_CODE_EXEC": "false",
    "ENABLE_API_CALL": "false",
    "CHAOS_MODE": "false",
    "PARALLEL_MODE": "false",
    "MULTI_AGENT_MODE": "false",
    "MAX_RETRIES": "2",
    "LOG_LEVEL": "WARNING",
}
os.environ.update(_PINNED_ENV)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from tests.unit.fakes import FakeLLM  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_singletons(monkeypatch):
    """Give every test fresh settings, breakers, chaos state and Redis fallback stores."""
    import config
    from agent.reliability import chaos, circuit_breaker
    from services import redis_service

    config.get_settings.cache_clear()
    from agent.graph import reset_graph

    reset_graph()
    circuit_breaker._manager = None
    chaos._chaos_middleware = None
    redis_service._redis_service = None

    from agent.reliability import retry

    # Zero backoff keeps retry tests fast without patching asyncio.sleep globally.
    monkeypatch.setattr(retry, "compute_backoff_seconds", lambda *args, **kwargs: 0.0)
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def settings():
    from config import get_settings

    return get_settings()


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr("services.llm_service._call_openai", fake.call_openai)
    return fake
