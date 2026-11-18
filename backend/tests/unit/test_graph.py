"""Full sequential graph runs against the fake LLM."""

from __future__ import annotations

import time

from agent.graph import route_after_validation, run_agent
from agent.state import create_initial_state
from models import StepDefinition, StepResult
from tests.unit.fakes import RETRY_VERDICT


def _result(validation: str, step_id: str = "step_1") -> StepResult:
    return StepResult(step_id=step_id, status="success", output="x" * 80, tokens_used=1, latency_ms=1,
                      model_used="m", retry_count=0, validation=validation)


def _state_with_steps(n: int = 2):
    state = create_initial_state("t1", "task")
    state["steps"] = [
        StepDefinition(step_id=f"step_{i}", name=f"s{i}", description="d", tool_needed="llm_only",
                       dependencies=[], estimated_complexity="low")
        for i in range(1, n + 1)
    ]
    return state


def test_retry_limit_comes_from_settings(settings):
    settings.MAX_RETRIES = 1
    state = _state_with_steps()
    state["step_results"] = [_result("retry")]
    assert route_after_validation(state) == "retry"
    state["retry_counts"]["step_1"] = 1
    assert route_after_validation(state) == "reflect"


def test_reflect_verdict_goes_to_reflector_not_finalizer():
    state = _state_with_steps()
    state["step_results"] = [_result("reflect")]
    state["reflection_counts"]["step_1"] = 5
    assert route_after_validation(state) == "reflect"


def test_token_budget_stops_the_run(settings):
    settings.TASK_TOKEN_BUDGET = 10
    state = _state_with_steps()
    state["step_results"] = [_result("pass")]
    state["llm_tokens_used"] = 11
    assert route_after_validation(state) == "budget"


async def test_happy_path_completes_every_step(fake_llm):
    final = await run_agent("task-1", "Summarise solar panel efficiency")
    assert final["status"] == "completed"
    assert [r.step_id for r in final["step_results"]] == ["step_1", "step_2"]
    assert final["final_output"]["summary"]["outcome"] == "complete"
    # planner + 2x(executor + validator) + finalizer, each 150 tokens
    assert final["llm_tokens_used"] == 150 * 6
    assert final["error_log"] == []  # passing verdicts are not errors
    assert len(final["validation_log"]) == 2


async def test_validator_feedback_reaches_the_retry(fake_llm):
    fake_llm.script("validator", RETRY_VERDICT)
    final = await run_agent("task-2", "Summarise solar panel efficiency")
    assert final["status"] == "completed"
    assert final["retry_counts"]["step_1"] == 1
    second_attempt = fake_llm.calls_for("executor")[1]["prompt"]
    assert "Reviewer feedback on the previous attempt (fix this): too vague" in second_attempt


async def test_persistent_failure_skips_the_step_and_continues(fake_llm, settings):
    settings.MAX_RETRIES = 1
    # step_1 fails validation on every attempt; reflector skips it; step_2 must still run.
    fake_llm.script("validator", RETRY_VERDICT, RETRY_VERDICT)
    final = await run_agent("task-3", "Summarise solar panel efficiency")
    statuses = {r.step_id: r.status for r in final["step_results"]}
    assert statuses["step_1"] == "skipped"
    assert statuses["step_2"] == "success"
    assert final["final_output"]["summary"]["outcome"] == "partial"


async def test_failed_executor_output_is_not_sent_to_the_judge(fake_llm, settings):
    from tests.unit.fakes import provider_error

    settings.MAX_RETRIES = 1
    # Every model in the chain rejects the first executor attempt (3 models, 1 call each).
    fake_llm.script("executor", *[provider_error(400)] * 3)
    await run_agent("task-4", "Summarise solar panel efficiency")
    judged_prompts = [call["prompt"] for call in fake_llm.calls_for("validator")]
    assert all("Output Produced:\n\n" not in prompt for prompt in judged_prompts)
    assert len(judged_prompts) == 2  # only the two successful step outputs were judged


async def test_redis_absent_does_not_slow_every_operation():
    from services.redis_service import get_redis_service

    service = get_redis_service()
    started = time.perf_counter()
    for index in range(50):
        await service.update_step_status("t", index, "running")
    assert time.perf_counter() - started < 1.0
