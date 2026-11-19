"""HTTP surface: admin guard, single-run guard, per-task agent metrics."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import main

    return TestClient(main.app)


def test_config_changes_refused_without_admin_key():
    client = _client()
    assert client.post("/config", json={"chaos_mode": True}).status_code == 403
    assert client.post("/config/chaos", json={"enabled": True}).status_code == 403
    assert client.get("/config").status_code == 200


def test_config_changes_need_matching_admin_key(settings):
    settings.ADMIN_API_KEY = "s3cret"
    client = _client()
    assert client.post("/config/retries", json={"value": 2}).status_code == 401
    ok = client.post("/config/retries", json={"value": 2}, headers={"X-Admin-Key": "s3cret"})
    assert ok.status_code == 200 and ok.json()["data"]["max_retries"] == 2


async def test_same_task_cannot_run_twice(monkeypatch):
    from routes import execute

    gate = asyncio.Event()

    async def slow_run(task_id):
        await gate.wait()

    monkeypatch.setattr(execute, "_run_execution", slow_run)
    assert execute.start_background_execution("t-1") is True
    assert execute.start_background_execution("t-1") is False
    gate.set()
    await asyncio.sleep(0)
    await asyncio.gather(*execute._running_tasks.values(), return_exceptions=True)


def test_agent_contributions_are_per_task():
    from agent.graph import agent_contributions
    from agent.state import create_initial_state
    from models import StepResult

    def state_with(agent: str, tokens: int):
        state = create_initial_state(f"task-{agent}", "x")
        state["step_results"] = [StepResult(step_id="step_1", status="success", output="o", tokens_used=tokens,
                                            latency_ms=1, model_used="m", retry_count=0, agent_name=agent,
                                            agent_role="analysis")]
        return state

    a = agent_contributions(state_with("Research Agent", 10))
    b = agent_contributions(state_with("Writing Agent", 20))
    assert list(a) == ["Research Agent"] and list(b) == ["Writing Agent"]
    assert a["Research Agent"]["total_tokens"] == 10


def test_create_plan_then_get_task(fake_llm):
    client = _client()
    created = client.post("/tasks", json={"task": "Summarise solar panel efficiency"}).json()
    assert created["success"] and created["data"]["status"] == "planned"
    task_id = created["data"]["task_id"]
    state = client.get(f"/tasks/{task_id}").json()["data"]
    assert state["status"] == "planned" and len(state["steps"]) == 2


def test_task_creation_is_rate_limited_per_client(fake_llm, settings):
    import security

    security._per_ip.clear()
    security._daily.clear()
    settings.TASKS_PER_IP_PER_HOUR = 2
    client = _client()
    codes = [client.post("/tasks", json={"task": "Summarise solar panel efficiency"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_daily_cap_applies_across_clients(fake_llm, settings):
    import security

    security._per_ip.clear()
    security._daily.clear()
    settings.DAILY_TASK_LIMIT = 1
    settings.TRUST_PROXY_HEADERS = True
    client = _client()
    first = client.post("/tasks", json={"task": "Summarise solar"}, headers={"X-Forwarded-For": "1.1.1.1"})
    second = client.post("/tasks", json={"task": "Summarise wind"}, headers={"X-Forwarded-For": "2.2.2.2"})
    assert (first.status_code, second.status_code) == (200, 429)
    security._daily.clear()


def test_role_key_client_is_rebuilt_after_refresh_interval(monkeypatch, settings):
    import services.llm_service as llm

    keys = iter(["key-1", "key-2"])
    monkeypatch.setattr(llm, "_bedrock_role_key", lambda region: next(keys))
    monkeypatch.setattr(llm, "_openai_client", None)
    settings.BEDROCK_ROLE_KEY_REGION = "ap-south-1"
    first = llm.get_openai_client()
    assert first.api_key == "key-1"
    assert llm.get_openai_client() is first  # cached within the interval
    monkeypatch.setattr(llm, "_openai_client_created_at", llm._openai_client_created_at - llm.BEDROCK_KEY_REFRESH_SECONDS - 1)
    assert llm.get_openai_client().api_key == "key-2"
    monkeypatch.setattr(llm, "_openai_client", None)
