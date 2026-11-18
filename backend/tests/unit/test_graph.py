"""Task graph: planning pause, scheduling, per-step retry/reflection, budgets, resume."""

from __future__ import annotations

import time

import pytest

from agent.graph import continue_task, dispatch, get_task_state, plan_task, run_agent
from agent.state import create_initial_state
from models import StepDefinition, StepResult
from tests.unit.fakes import RETRY_VERDICT


def _steps(*deps: list[str]) -> list[StepDefinition]:
    return [StepDefinition(step_id=f"step_{i}", name=f"s{i}", description="d", tool_needed="llm_only",
                           dependencies=d, estimated_complexity="low") for i, d in enumerate(deps, start=1)]


def _done(step_id: str) -> StepResult:
    return StepResult(step_id=step_id, status="success", output="ok", retry_count=0)


def test_dispatch_sends_only_ready_steps(settings):
    settings.PARALLEL_MODE = True
    state = create_initial_state("t", "task")
    state["steps"] = _steps([], [], ["step_1", "step_2"])
    sends = dispatch(state)
    assert [send.arg["step"].step_id for send in sends] == ["step_1", "step_2"]
    state["step_results"] = [_done("step_1"), _done("step_2")]
    sends = dispatch(state)
    assert [send.arg["step"].step_id for send in sends] == ["step_3"]
    assert "step_1 (success): ok" in sends[0].arg["context"]


def test_dispatch_is_one_at_a_time_without_parallel_mode(settings):
    state = create_initial_state("t", "task")
    state["steps"] = _steps([], [])
    assert len(dispatch(state)) == 1


def test_dispatch_finalizes_when_done_and_stops_on_budget(settings):
    state = create_initial_state("t", "task")
    state["steps"] = _steps([])
    state["step_results"] = [_done("step_1")]
    assert dispatch(state) == "finalizer"
    state["step_results"] = []
    settings.TASK_TOKEN_BUDGET = 10
    state["llm_tokens_used"] = 11
    assert dispatch(state) == "budget_stop"


async def test_plan_pauses_before_any_step_runs(fake_llm):
    state = await plan_task("plan-1", "Summarise solar panel efficiency")
    assert state["status"] == "planned"
    assert [s.step_id for s in state["steps"]] == ["step_1", "step_2"]
    assert state["step_results"] == []
    assert fake_llm.calls_for("executor") == []
    final = await continue_task("plan-1")
    assert final["status"] == "completed"


async def test_happy_path_counts_every_model_call(fake_llm):
    final = await run_agent("task-1", "Summarise solar panel efficiency")
    assert final["status"] == "completed"
    assert [r.step_id for r in final["step_results"]] == ["step_1", "step_2"]
    assert final["final_output"]["summary"]["outcome"] == "complete"
    # planner + per step (supervisor + worker + judge) x 2 + finalizer = 8 calls of 150 tokens
    assert len(final["llm_calls"]) == 8
    assert final["llm_tokens_used"] == 8 * 150
    assert final["final_output"]["summary"]["total_tokens"] == 8 * 150
    assert {c["node"] for c in final["llm_calls"]} == {"planner", "execute", "validate", "finalizer"}
    assert final["error_log"] == []


async def test_supervisor_brief_reaches_the_worker(fake_llm):
    final = await run_agent("task-brief", "Summarise solar panel efficiency")
    worker_prompt = fake_llm.calls_for("executor")[0]["prompt"]
    assert "Brief from the supervisor:\nProduce the facts this step asks for" in worker_prompt
    briefs = [m for m in final["agent_messages"] if m["kind"] == "brief"]
    assert {m["recipient"] for m in briefs} == {"analysis"}
    assert final["agent_contributions"]["Analysis Agent"]["steps_handled"] == 2


async def test_validator_feedback_reaches_the_retry(fake_llm):
    fake_llm.script("validator", RETRY_VERDICT)
    final = await run_agent("task-2", "Summarise solar panel efficiency")
    assert final["status"] == "completed"
    assert final["retry_counts"]["step_1"] == 1
    second_attempt = fake_llm.calls_for("executor")[1]["prompt"]
    assert "Reviewer feedback on the previous attempt (fix this): too vague" in second_attempt


async def test_persistent_failure_skips_the_step_and_continues(fake_llm, settings):
    settings.MAX_RETRIES = 1
    fake_llm.script("validator", RETRY_VERDICT, RETRY_VERDICT)
    final = await run_agent("task-3", "Summarise solar panel efficiency")
    statuses = {r.step_id: r.status for r in final["step_results"]}
    assert statuses == {"step_1": "skipped", "step_2": "success"}
    assert final["final_output"]["summary"]["outcome"] == "partial"


async def test_reflector_modify_reruns_the_rewritten_step(fake_llm, settings):
    settings.MAX_RETRIES = 0
    fake_llm.script("validator", RETRY_VERDICT)
    fake_llm.script("reflector", {"action": "MODIFY_STEP", "reasoning": "too broad",
                                  "modified_step": "List three facts about solar panel efficiency", "sub_steps": []})
    final = await run_agent("task-modify", "Summarise solar panel efficiency")
    assert {r.step_id: r.status for r in final["step_results"]}["step_1"] == "success"
    rerun_prompt = fake_llm.calls_for("executor")[1]["prompt"]
    assert "The reflector rewrote this step" in rerun_prompt


async def test_reflector_decompose_runs_sub_steps(fake_llm, settings):
    settings.MAX_RETRIES = 0
    fake_llm.script("validator", RETRY_VERDICT)
    fake_llm.script("reflector", {"action": "DECOMPOSE", "reasoning": "too big", "sub_steps": [
        {"name": "part a", "description": "Collect efficiency numbers"},
        {"name": "part b", "description": "Collect cost numbers"}]})
    final = await run_agent("task-decompose", "Summarise solar panel efficiency")
    step_1 = {r.step_id: r for r in final["step_results"]}["step_1"]
    assert step_1.status == "success" and "[part a]" in step_1.output and "[part b]" in step_1.output


async def test_abort_fails_the_task_and_stops_scheduling(fake_llm, settings):
    settings.MAX_RETRIES = 0
    fake_llm.script("validator", RETRY_VERDICT)
    fake_llm.script("reflector", {"action": "ABORT", "reasoning": "impossible", "sub_steps": []})
    final = await run_agent("task-abort", "Summarise solar panel efficiency")
    assert final["status"] == "failed"
    assert [r.step_id for r in final["step_results"]] == ["step_1"]  # step_2 never dispatched
    assert final["final_output"]["summary"]["abort_reason"] == "impossible"


async def test_parallel_steps_run_concurrently_and_merge(fake_llm, settings):
    settings.PARALLEL_MODE = True
    fake_llm.plan = [
        {"step_id": "step_1", "name": "A", "description": "Collect facts about solar panel efficiency",
         "tool_needed": "llm_only", "dependencies": [], "estimated_complexity": "low"},
        {"step_id": "step_2", "name": "B", "description": "Collect facts about wind turbine efficiency",
         "tool_needed": "llm_only", "dependencies": [], "estimated_complexity": "low"},
        {"step_id": "step_3", "name": "C", "description": "Compare solar panel and wind turbine efficiency",
         "tool_needed": "llm_only", "dependencies": ["step_1", "step_2"], "estimated_complexity": "low"},
    ]
    final = await run_agent("task-par", "Compare solar and wind")
    assert final["execution_levels"] == [["step_1", "step_2"], ["step_3"]]
    assert sorted(r.step_id for r in final["step_results"]) == ["step_1", "step_2", "step_3"]
    third_prompt = [c for c in fake_llm.calls_for("executor") if "Your step (step_3)" in c["prompt"]][0]["prompt"]
    assert "- step_1 (success)" in third_prompt and "- step_2 (success)" in third_prompt


async def test_resume_after_crash_reruns_only_unfinished_steps(fake_llm, monkeypatch):
    from agent import step_graph

    await plan_task("task-crash", "Summarise solar panel efficiency")
    original = step_graph.STEP_GRAPH.ainvoke
    calls = {"n": 0}

    class SimulatedCrash(BaseException):
        """Stands in for the process dying (not an ordinary exception a step could catch)."""

    async def crash_on_second_step(payload, config=None):
        calls["n"] += 1
        if payload["step"].step_id == "step_2" and calls["n"] == 2:
            raise SimulatedCrash("process killed")
        return await original(payload, config=config)

    monkeypatch.setattr(step_graph.STEP_GRAPH, "ainvoke", crash_on_second_step)
    with pytest.raises(SimulatedCrash):
        await continue_task("task-crash")
    saved = await get_task_state("task-crash")
    assert [r.step_id for r in saved["step_results"]] == ["step_1"]  # step_1 survived in the checkpoint

    executor_calls_before = len(fake_llm.calls_for("executor"))
    final = await continue_task("task-crash")
    assert final["status"] == "completed"
    assert [r.step_id for r in final["step_results"]] == ["step_1", "step_2"]
    assert len(fake_llm.calls_for("executor")) == executor_calls_before + 1  # only step_2 re-ran


async def test_redis_absent_does_not_slow_every_operation():
    from services.redis_service import get_redis_service

    service = get_redis_service()
    started = time.perf_counter()
    for index in range(50):
        await service.update_step_status("t", index, "running")
    assert time.perf_counter() - started < 1.0


async def test_events_replay_for_late_subscribers():
    from services.redis_service import get_redis_service

    service = get_redis_service()
    for event_type in ["task_started", "step_started", "task_completed"]:
        await service.publish_event("late", {"event_type": event_type, "timestamp": "t"})
    seen = [event["event_type"] async for event in service.subscribe_events("late")]
    assert seen == ["task_started", "step_started", "task_completed"]


async def test_unexpected_error_fails_only_that_step(fake_llm, monkeypatch):
    from agent import step_graph

    async def broken_judge(*_args, **_kwargs):
        raise KeyError("bug in judge")

    monkeypatch.setattr(step_graph, "judge_output", broken_judge)
    final = await run_agent("task-bug", "Summarise solar panel efficiency")
    assert {r.step_id: r.status for r in final["step_results"]} == {"step_1": "failed", "step_2": "failed"}
    assert final["status"] in {"completed", "failed"} and final["final_output"] is not None
    assert any(e["error_type"] == "STEP_CRASH" for e in final["error_log"])
