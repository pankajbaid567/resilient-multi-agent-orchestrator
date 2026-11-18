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
    from agent.multi_agent.coordinator import AgentCoordinator
    from agent.state import create_initial_state
    from models import StepResult

    def state_with(agent: str, tokens: int):
        state = create_initial_state(f"task-{agent}", "x")
        state["step_results"] = [StepResult(step_id="step_1", status="success", output="o", tokens_used=tokens,
                                            latency_ms=1, model_used="m", retry_count=0, agent_name=agent,
                                            agent_role="analysis")]
        return state

    a = AgentCoordinator.contributions_from_state(state_with("Research Agent", 10))
    b = AgentCoordinator.contributions_from_state(state_with("Writing Agent", 20))
    assert list(a) == ["Research Agent"] and list(b) == ["Writing Agent"]
    assert a["Research Agent"]["total_tokens"] == 10
