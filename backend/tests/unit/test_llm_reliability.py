"""Error classification, retry and fallback behaviour of the LLM layer."""

from __future__ import annotations

import asyncio

import pytest

from agent.reliability.circuit_breaker import CircuitBreaker, get_circuit_breaker_manager
from agent.reliability.fallback import AllProvidersFailedError, call_with_fallback
from services.llm_service import (
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
    _map_provider_error,
    call_llm,
)
from tests.unit.fakes import provider_error

CHAIN = [
    {"provider": "open_source", "model": "model-a", "label": "A"},
    {"provider": "open_source", "model": "model-b", "label": "B"},
]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, LLMRequestError), (404, LLMRequestError), (429, LLMRateLimitError), (503, LLMServerError)],
)
def test_status_codes_map_to_typed_errors(status, expected):
    assert isinstance(_map_provider_error(provider_error(status), "open_source", "m"), expected)


def test_timeout_maps_to_timeout_error():
    assert isinstance(_map_provider_error(asyncio.TimeoutError(), "open_source", "m"), LLMTimeoutError)


async def test_client_error_is_not_retried_but_falls_back(fake_llm):
    fake_llm.script("executor", provider_error(401))
    response = await call_with_fallback("hi", fallback_chain=CHAIN)
    models = [call["model"] for call in fake_llm.calls]
    assert models == ["model-a", "model-b"]  # one attempt on A (no retry), then B
    assert response.model_used == "model-b"


async def test_server_error_is_retried_on_same_model(fake_llm):
    fake_llm.script("executor", provider_error(503))
    response = await call_with_fallback("hi", fallback_chain=CHAIN)
    assert [call["model"] for call in fake_llm.calls] == ["model-a", "model-a"]
    assert response.model_used == "model-a"


async def test_all_providers_failing_raises_with_every_error(fake_llm):
    fake_llm.script("executor", *[provider_error(503)] * 4)
    with pytest.raises(AllProvidersFailedError) as info:
        await call_with_fallback("hi", fallback_chain=CHAIN)
    assert [entry["provider"] for entry in info.value.errors] == ["open_source/model-a", "open_source/model-b"]


async def test_usage_split_is_recorded(fake_llm):
    response = await call_llm("hi", model="model-a")
    assert (response.input_tokens, response.output_tokens, response.tokens_used) == (100, 50, 150)


async def test_json_mode_retry_without_response_format_only_on_400(monkeypatch):
    import services.llm_service as llm

    seen: list[dict] = []

    class Completions:
        async def create(self, **kwargs):
            seen.append(kwargs)
            raise asyncio.TimeoutError()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(llm, "get_openai_client", lambda: Client())
    with pytest.raises(LLMTimeoutError):
        await call_llm("hi", json_mode=True, timeout=1)
    assert len(seen) == 1  # a timeout must not trigger the response_format fallback


async def test_chaos_failure_is_seen_by_the_retry_layer(fake_llm, settings, monkeypatch):
    from agent.reliability import chaos

    settings.CHAOS_MODE = True
    rolls = iter([0.40, 0.99])  # first call: injected empty response; second: pass through
    monkeypatch.setattr(chaos.random, "random", lambda: next(rolls))
    response = await call_with_fallback("hi", fallback_chain=CHAIN)
    # The injected failure was retried on the same model instead of skipping to model-b.
    assert response.model_used == "model-a"
    assert chaos.get_chaos_middleware().get_stats()["empty"] == 1


async def test_turning_chaos_off_closes_circuits(settings):
    from agent.reliability.chaos import set_chaos_mode

    manager = get_circuit_breaker_manager()
    for _ in range(3):
        await manager.record_failure("open_source/model-a")
    assert await manager.is_open("open_source/model-a")
    set_chaos_mode(False)
    assert not await manager.is_open("open_source/model-a")


async def test_circuit_breaker_opens_and_half_opens(monkeypatch):
    from agent.reliability import circuit_breaker as cb

    clock = [1000.0]
    monkeypatch.setattr(cb, "time", lambda: clock[0])
    breaker = CircuitBreaker("p", failure_threshold=0.5, min_calls=3, cooldown_seconds=10)
    for _ in range(3):
        await breaker.record_failure()
    assert await breaker.is_open()
    clock[0] += 11
    assert not await breaker.is_open()  # the single half-open probe is allowed
    assert await breaker.is_open()  # a second caller is held back while the probe runs
    await breaker.record_success()
    assert (await breaker.get_state())["state"] == "closed"


async def test_cancelled_probe_releases_the_half_open_slot(monkeypatch):
    from agent.reliability import circuit_breaker as cb

    clock = [1000.0]
    monkeypatch.setattr(cb, "time", lambda: clock[0])
    manager = get_circuit_breaker_manager()
    key = "open_source/model-a"
    breaker = await manager.get_breaker(key)
    breaker.cooldown_seconds = 10
    for _ in range(3):
        await manager.record_failure(key)
    clock[0] += 11

    started = asyncio.Event()

    async def hanging_call(**_kwargs):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr("agent.reliability.fallback.call_llm", hanging_call)
    task = asyncio.create_task(call_with_fallback("hi", fallback_chain=CHAIN[:1], circuit_breaker=manager))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await manager.is_open(key)  # the probe slot is free again


def test_model_chain_is_configurable(settings):
    from agent.reliability.fallback import default_chain

    settings.MODEL_CHAIN = "gemini-flash, gemini-flash-lite ,gemini-flash"
    assert [entry["model"] for entry in default_chain()] == ["gemini-flash", "gemini-flash-lite"]
    assert [entry["model"] for entry in default_chain("gemini-pro")] == ["gemini-pro", "gemini-flash", "gemini-flash-lite"]


async def test_temperature_can_be_omitted(monkeypatch, settings):
    import services.llm_service as llm

    sent: dict = {}

    class Completions:
        async def create(self, **kwargs):
            sent.update(kwargs)
            raise asyncio.TimeoutError()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(llm, "get_openai_client", lambda: Client())
    settings.LLM_SEND_TEMPERATURE = False
    with pytest.raises(LLMTimeoutError):
        await call_llm("hi", timeout=1)
    assert "temperature" not in sent


async def test_every_role_survives_a_primary_model_outage(fake_llm, settings, monkeypatch):
    """Planner, supervisor, judge and finalizer must fall back too, not only workers."""
    from agent.graph import run_agent

    settings.MODEL_CHAIN = "primary-model,backup-model"
    original = fake_llm.call_openai

    async def primary_down(**kwargs):
        if kwargs["model"] == "primary-model":
            raise provider_error(503)
        return await original(**kwargs)

    monkeypatch.setattr("services.llm_service._call_openai", primary_down)
    final = await run_agent("outage", "Summarise solar panel efficiency")
    assert final["status"] == "completed"
    assert final["final_output"]["summary"]["outcome"] == "complete"
    assert "primary-model" not in {c["model"] for c in final["llm_calls"] if c["ok"]}
    assert {c["node"] for c in final["llm_calls"] if c["model"] == "backup-model" and c["ok"]} >= {"planner", "execute", "finalizer"}
