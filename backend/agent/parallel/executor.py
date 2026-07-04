"""Parallel level executor for DAG-based step execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from time import perf_counter
from typing import Any, List, Optional, Tuple

from agent.multi_agent.coordinator import get_agent_coordinator
from agent.nodes.executor import (
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    _build_agent_step_for_execution,
    _call_llm_with_reliability,
    _extract_python_code,
    _provider_for_model as _executor_provider_for_model,
    _routing_reason_for_step,
    _safe_api_call,
    _safe_generate_code,
    _safe_web_search,
)
from agent.nodes.reflector import reflector_node
from agent.nodes.validator import (
    VALIDATOR_SYSTEM_PROMPT,
    VALIDATOR_USER_PROMPT_TEMPLATE,
    _get_validation_model,
    _parse_validation_response,
    _provider_for_model as _validator_provider_for_model,
    rule_based_validate,
)
from agent.parallel.dag import ExecutionDAG
from agent.state import AgentState, create_initial_state
from config import get_settings
from models import StepDefinition, StepResult, TraceEntry
from services.llm_service import LLMError, LLMResponseError, call_llm
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)


class ParallelExecutor:
    """Executes steps in parallel within dependency levels."""

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max(1, int(max_concurrent))
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self._tokens_lock = asyncio.Lock()  # For atomic token counting

    async def _execute_single_step(
        self,
        step: StepDefinition,
        state: AgentState,
        level: int,
        concurrent_ids: List[str],
    ) -> Tuple[StepResult, List[dict]]:
        """Execute a single step with full reliability wrapping.
        Returns: (StepResult, list of trace events generated)

        This is a self-contained execution:
        1. Publish step_started event
        2. Dispatch tool if needed
        3. Call LLM with fallback
        4. Validate output
        5. If retry needed: retry within this function
        6. If reflect needed: call reflector
        7. Return final result
        """
        async with self.semaphore:
            settings = get_settings()
            max_retries = max(0, int(getattr(settings, "MAX_RETRIES", 3)))
            step_timeout_seconds = max(1, int(getattr(settings, "STEP_TIMEOUT", 60)))

            trace_events: List[dict] = []
            retry_count = 0
            reflection_rounds = 0
            working_step = step

            await self._append_trace(
                task_id=state["task_id"],
                trace_events=trace_events,
                event_type="step_started",
                step_id=step.step_id,
                step_name=step.name,
                details={
                    "level": level,
                    "parallel": True,
                    "concurrent_ids": sorted(concurrent_ids),
                    "tool_needed": step.tool_needed,
                },
                level=level,
                concurrent_with=concurrent_ids,
            )

            while True:
                attempt_number = retry_count + 1
                attempt_started = perf_counter()

                try:
                    (
                        attempt_result,
                        validator_verdict,
                        validator_reason,
                        validator_scores,
                        fallback_transition,
                        agent_assignment,
                    ) = await asyncio.wait_for(
                        self._execute_attempt(
                            step=working_step,
                            state=state,
                            retry_count=retry_count,
                        ),
                        timeout=step_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    timeout_error = (
                        f"Step {working_step.step_id} timed out after "
                        f"{step_timeout_seconds} seconds in parallel execution"
                    )
                    attempt_result = StepResult(
                        step_id=working_step.step_id,
                        status="failed",
                        output="",
                        tokens_used=0,
                        latency_ms=step_timeout_seconds * 1000,
                        model_used="",
                        tool_used=working_step.tool_needed if working_step.tool_needed != "none" else None,
                        tool_result=None,
                        retry_count=retry_count,
                        validation="retry",
                        error=timeout_error,
                    )
                    validator_verdict = "retry"
                    validator_reason = timeout_error
                    validator_scores = {}
                    fallback_transition = None
                    agent_assignment = None

                latency_ms = max(1, int((perf_counter() - attempt_started) * 1000))

                if agent_assignment is not None:
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="agent_assigned",
                        step_id=working_step.step_id,
                        step_name=working_step.name,
                        details={
                            "agent_name": agent_assignment.get("agent_name"),
                            "agent_role": agent_assignment.get("agent_role"),
                            "routing_reason": agent_assignment.get("routing_reason"),
                            "attempt": attempt_number,
                            "parallel": True,
                        },
                        level=level,
                        concurrent_with=concurrent_ids,
                    )

                if fallback_transition is not None:
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="fallback_triggered",
                        step_id=working_step.step_id,
                        step_name=working_step.name,
                        details={
                            "from_provider": fallback_transition.get("from_provider"),
                            "to_provider": fallback_transition.get("to_provider"),
                            "attempt": attempt_number,
                        },
                        level=level,
                        concurrent_with=concurrent_ids,
                    )

                if attempt_result.status == "success" and validator_verdict == "pass":
                    attempt_result.validation = "pass"
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="step_completed",
                        step_id=attempt_result.step_id,
                        step_name=working_step.name,
                        details={
                            "validator_verdict": "pass",
                            "validator_reason": validator_reason,
                            "scores": validator_scores,
                            "retry_count": retry_count,
                            "parallel": True,
                            "attempt": attempt_number,
                        },
                        duration_ms=attempt_result.latency_ms,
                        tokens_used=attempt_result.tokens_used,
                        model_used=attempt_result.model_used,
                        level=level,
                        concurrent_with=concurrent_ids,
                    )
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="parallel_step_completed",
                        step_id=attempt_result.step_id,
                        step_name=working_step.name,
                        details={
                            "status": "success",
                            "level": level,
                            "latency_ms": attempt_result.latency_ms,
                            "retry_count": retry_count,
                        },
                        duration_ms=attempt_result.latency_ms,
                        tokens_used=attempt_result.tokens_used,
                        model_used=attempt_result.model_used,
                        level=level,
                        concurrent_with=concurrent_ids,
                    )
                    return attempt_result, trace_events

                verdict = validator_verdict if validator_verdict in {"retry", "reflect"} else "retry"
                attempt_error = attempt_result.error or validator_reason or "Step execution failed"
                attempt_result.validation = verdict

                await self._append_trace(
                    task_id=state["task_id"],
                    trace_events=trace_events,
                    event_type="step_failed",
                    step_id=working_step.step_id,
                    step_name=working_step.name,
                    details={
                        "validator_verdict": verdict,
                        "validator_reason": validator_reason,
                        "scores": validator_scores,
                        "attempt": attempt_number,
                        "retry_count": retry_count,
                        "parallel": True,
                    },
                    duration_ms=latency_ms,
                    tokens_used=attempt_result.tokens_used,
                    model_used=attempt_result.model_used,
                    error=attempt_error,
                    level=level,
                    concurrent_with=concurrent_ids,
                )

                should_retry = verdict == "retry" and retry_count < max_retries
                if should_retry:
                    retry_count += 1
                    state["retry_counts"][working_step.step_id] = retry_count
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="retry_triggered",
                        step_id=working_step.step_id,
                        step_name=working_step.name,
                        details={
                            "retry_count": retry_count,
                            "reason": attempt_error,
                            "parallel": True,
                        },
                        level=level,
                        concurrent_with=concurrent_ids,
                    )
                    continue

                reflection_rounds += 1
                reflection_outcome = await self._run_reflection(
                    step=working_step,
                    state=state,
                    failed_result=attempt_result,
                    retry_count=retry_count,
                    level=level,
                    concurrent_ids=concurrent_ids,
                )
                trace_events.extend(reflection_outcome["trace_events"])

                final_result = reflection_outcome.get("final_result")
                if isinstance(final_result, StepResult):
                    await self._append_trace(
                        task_id=state["task_id"],
                        trace_events=trace_events,
                        event_type="parallel_step_completed",
                        step_id=final_result.step_id,
                        step_name=working_step.name,
                        details={
                            "status": final_result.status,
                            "level": level,
                            "retry_count": retry_count,
                            "reflection_rounds": reflection_rounds,
                        },
                        duration_ms=final_result.latency_ms,
                        tokens_used=final_result.tokens_used,
                        model_used=final_result.model_used,
                        error=final_result.error,
                        level=level,
                        concurrent_with=concurrent_ids,
                    )
                    return final_result, trace_events

                updated_step = reflection_outcome.get("updated_step")
                if isinstance(updated_step, StepDefinition) and reflection_rounds <= 3:
                    working_step = updated_step
                    retry_count = 0
                    state["retry_counts"][working_step.step_id] = 0
                    continue

                failure_reason = reflection_outcome.get("reason") or attempt_error
                failed_final = attempt_result.model_copy(
                    update={
                        "status": "failed",
                        "validation": "reflect",
                        "error": str(failure_reason),
                    }
                )
                await self._append_trace(
                    task_id=state["task_id"],
                    trace_events=trace_events,
                    event_type="parallel_step_completed",
                    step_id=failed_final.step_id,
                    step_name=working_step.name,
                    details={
                        "status": "failed",
                        "level": level,
                        "retry_count": retry_count,
                        "reflection_rounds": reflection_rounds,
                    },
                    duration_ms=failed_final.latency_ms,
                    tokens_used=failed_final.tokens_used,
                    model_used=failed_final.model_used,
                    error=failed_final.error,
                    level=level,
                    concurrent_with=concurrent_ids,
                )
                return failed_final, trace_events

    async def execute_level(
        self,
        level_index: int,
        step_ids: List[str],
        state: AgentState,
    ) -> AgentState:
        """Execute all steps in a parallel level concurrently.

        Uses asyncio.gather() with return_exceptions=True.

        Steps share the same input context (from prior levels).
        Steps do NOT see each other's results (they're concurrent).

        After all steps complete:
        - Collect all results
        - Update state with results
        - Save checkpoint

        Failure handling:
        - If a step fails: its result is recorded as failed
        - Other steps in the level are NOT affected
        - Failed steps are handled by reflection in a post-level phase
        """
        step_map = {step.step_id: step for step in state.get("steps", [])}
        redis = get_redis_service()

        normalized_step_ids = [step_id for step_id in step_ids if step_id in step_map]
        if not normalized_step_ids:
            state["status"] = "failed"
            message = f"Parallel level {level_index} has no valid step IDs"
            state["error_log"].append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "step_id": "level",
                    "error_type": "PARALLEL_LEVEL_EMPTY",
                    "error_message": message,
                }
            )
            await self._append_trace(
                task_id=state["task_id"],
                trace_events=state["execution_trace"],
                event_type="task_failed",
                details={"reason": message, "level": level_index},
                error=message,
            )
            return state

        await self._publish_event(
            task_id=state["task_id"],
            event_type="parallel_level_started",
            data={
                "level": level_index,
                "step_count": len(normalized_step_ids),
                "step_ids": normalized_step_ids,
            },
        )

        state["execution_trace"].append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="parallel_level_started",
                details={
                    "level": level_index,
                    "step_count": len(normalized_step_ids),
                    "step_ids": normalized_step_ids,
                },
                level=level_index,
            ).model_dump()
        )

        step_index_by_id = {
            step.step_id: index
            for index, step in enumerate(state.get("steps", []))
        }
        for step_id in normalized_step_ids:
            step_index = step_index_by_id.get(step_id)
            if step_index is None:
                continue
            await self._safe_update_step_status(task_id=state["task_id"], step_index=step_index, status="running")

        tasks = [
            asyncio.create_task(
                self._execute_single_step(
                    step=step_map[step_id],
                    state=state,
                    level=level_index,
                    concurrent_ids=[sid for sid in normalized_step_ids if sid != step_id],
                )
            )
            for step_id in normalized_step_ids
        ]

        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        level_results: List[StepResult] = []
        failed_count = 0
        success_count = 0
        skipped_count = 0

        for step_id, outcome in zip(normalized_step_ids, outcomes):
            if isinstance(outcome, Exception):
                logger.exception(
                    "parallel_step_exception task_id=%s level=%s step_id=%s error=%s",
                    state.get("task_id"),
                    level_index,
                    step_id,
                    outcome,
                )
                result = StepResult(
                    step_id=step_id,
                    status="failed",
                    output="",
                    tokens_used=0,
                    latency_ms=0,
                    model_used="",
                    tool_used=step_map[step_id].tool_needed if step_map[step_id].tool_needed != "none" else None,
                    tool_result=None,
                    retry_count=state["retry_counts"].get(step_id, 0),
                    validation="reflect",
                    error=str(outcome),
                )
                state["execution_trace"].append(
                    TraceEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        event_type="step_failed",
                        step_id=step_id,
                        step_name=step_map[step_id].name,
                        details={
                            "parallel": True,
                            "level": level_index,
                            "reason": "Unhandled exception in asyncio.gather",
                        },
                        error=str(outcome),
                        level=level_index,
                    ).model_dump()
                )
            else:
                result, trace_events = outcome
                state["execution_trace"].extend(trace_events)

            level_results.append(result)
            state["step_results"].append(result)

            step_index = step_index_by_id.get(step_id)
            if step_index is not None:
                persisted_status = result.status if result.status in {"success", "failed", "skipped"} else "failed"
                await self._safe_update_step_status(
                    task_id=state["task_id"],
                    step_index=step_index,
                    status=persisted_status,
                )

            if result.status == "success":
                success_count += 1
                takeaway = (result.output or "").strip()[:200]
                if takeaway:
                    state["context_memory"].append(takeaway)
            elif result.status == "skipped":
                skipped_count += 1
            else:
                failed_count += 1
                state["error_log"].append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "step_id": result.step_id,
                        "error_type": "PARALLEL_STEP_FAILED",
                        "error_message": result.error or "Parallel step failed",
                        "level": level_index,
                    }
                )

        state["current_step_index"] = min(len(state.get("steps", [])), len(state.get("step_results", [])))

        level_summary = {
            "level": level_index,
            "step_count": len(normalized_step_ids),
            "success": success_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }

        await self._publish_event(
            task_id=state["task_id"],
            event_type="parallel_level_completed",
            data={
                "level": level_index,
                "results_summary": level_summary,
            },
        )

        state["execution_trace"].append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="parallel_level_completed",
                details={
                    "level": level_index,
                    "results_summary": level_summary,
                },
                level=level_index,
            ).model_dump()
        )

        if failed_count > 0:
            state["status"] = "failed"
        else:
            state["status"] = "executing"

        try:
            await redis.save_checkpoint(state["task_id"], state)
            await redis.publish_event(
                state["task_id"],
                {
                    "event_type": "checkpoint_saved",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "node": "parallel_executor",
                    "level": level_index,
                    "status": state["status"],
                },
            )
        except Exception as exc:  # pragma: no cover - checkpointing is non-fatal
            logger.warning("parallel_checkpoint_save_failed task_id=%s level=%s error=%s", state["task_id"], level_index, exc)

        return state

    async def execute_dag(self, dag: ExecutionDAG, state: AgentState) -> AgentState:
        """Execute the full DAG level by level.

        for level_index, level_step_ids in enumerate(dag.get_execution_levels()):
            state = await self.execute_level(level_index, level_step_ids, state)
            # Check for ABORT from any reflection
            if state["status"] == "failed":
                break

        Publish events:
        - parallel_level_started: {level, step_count, step_ids}
        - parallel_level_completed: {level, results_summary}
        """
        is_valid, validation_error = dag.validate()
        if not is_valid:
            message = validation_error or "Execution DAG validation failed"
            self._mark_task_failed(state, error_type="DAG_VALIDATION_ERROR", message=message)
            return state

        try:
            levels = dag.get_execution_levels()
        except ValueError as exc:
            self._mark_task_failed(state, error_type="DEADLOCK_DETECTED", message=str(exc))
            return state

        state["execution_levels"] = levels
        state["status"] = "executing"

        completed: set[str] = {
            result.step_id
            for result in state.get("step_results", [])
            if result.status in {"success", "skipped"}
        }

        for level_index, level_step_ids in enumerate(levels):
            pending_step_ids = [step_id for step_id in level_step_ids if step_id not in completed]
            if not pending_step_ids:
                continue

            ready_steps = set(dag.get_ready_steps(completed))
            missing_ready = sorted(step_id for step_id in pending_step_ids if step_id not in ready_steps)
            if missing_ready:
                message = (
                    f"Deadlock/inconsistent scheduling at level {level_index}; "
                    f"steps not ready: {missing_ready}"
                )
                self._mark_task_failed(state, error_type="DEADLOCK_DETECTED", message=message)
                break

            before_completed = len(completed)
            state = await self.execute_level(level_index=level_index, step_ids=pending_step_ids, state=state)
            if state.get("status") == "failed":
                break

            step_result_by_id = {
                result.step_id: result
                for result in state.get("step_results", [])
                if result.step_id in set(pending_step_ids)
            }
            for step_id in pending_step_ids:
                result = step_result_by_id.get(step_id)
                if result is None:
                    continue
                if result.status in {"success", "skipped"}:
                    completed.add(step_id)

            if len(completed) == before_completed:
                message = (
                    f"Deadlock detected after level {level_index}; "
                    "no additional steps were marked completed"
                )
                self._mark_task_failed(state, error_type="DEADLOCK_DETECTED", message=message)
                break

        unresolved_steps = sorted(step_id for step_id in dag.nodes if step_id not in completed)
        if unresolved_steps and state.get("status") != "failed":
            message = f"Parallel execution finished with unresolved steps: {unresolved_steps}"
            self._mark_task_failed(state, error_type="PARALLEL_UNRESOLVED_STEPS", message=message)

        return state

    async def _execute_attempt(
        self,
        step: StepDefinition,
        state: AgentState,
        retry_count: int,
    ) -> Tuple[
        StepResult,
        str,
        str,
        dict[str, int],
        Optional[dict[str, str]],
        Optional[dict[str, str]],
    ]:
        """Execute one attempt for a single step and return validation metadata."""
        started = perf_counter()
        context_block = self._build_context_block(state=state)
        runtime_settings = get_settings()
        multi_agent_mode = bool(getattr(runtime_settings, "MULTI_AGENT_MODE", True))

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            step_number=0,
            total_steps=max(1, len(state.get("steps", []))),
        )
        user_prompt = USER_PROMPT_TEMPLATE.format(
            original_input=state.get("original_input", ""),
            step_id=step.step_id,
            step_name=step.name,
            step_description=step.description,
            formatted_prior_results=context_block,
        )

        tool_used: str | None = None
        tool_result: dict[str, Any] | None = None
        selected_agent_name: str | None = None
        selected_agent_role: str | None = None
        routing_reason: str | None = None

        try:
            if step.tool_needed == "none" and not multi_agent_mode:
                output = step.description.strip() or step.name
                result = StepResult(
                    step_id=step.step_id,
                    status="success",
                    output=output,
                    tokens_used=0,
                    latency_ms=max(1, int((perf_counter() - started) * 1000)),
                    model_used="",
                    tool_used=None,
                    tool_result=None,
                    retry_count=retry_count,
                    validation="pass",
                )
                return result, "pass", "No validation needed for pass-through step", {}, None, None

            if step.tool_needed == "web_search":
                tool_used = "web_search"
                tool_result = _safe_web_search(step.description)
                if tool_result.get("success"):
                    user_prompt = f"{user_prompt}\n\nSearch Results: {json.dumps(tool_result, ensure_ascii=True)}"
                else:
                    tool_used = None
                    tool_result = None

            elif step.tool_needed == "api_call":
                tool_used = "api_call"
                tool_result = _safe_api_call(step.description)
                if tool_result.get("success"):
                    user_prompt = f"{user_prompt}\n\nAPI Response: {json.dumps(tool_result, ensure_ascii=True)}"
                else:
                    tool_used = None
                    tool_result = None

            elif step.tool_needed == "code_exec":
                tool_used = "code_exec"
                code_response = await _safe_generate_code(
                    original_input=state.get("original_input", ""),
                    step_name=step.name,
                    step_description=step.description,
                    task_id=state["task_id"],
                    step_id=step.step_id,
                )
                if code_response is None:
                    tool_used = None
                    tool_result = None
                else:
                    generated_code = _extract_python_code(code_response.text)
                    from agent.tools import execute_python_code  # Local import to avoid startup overhead

                    execution_result = execute_python_code(generated_code)
                    if execution_result.get("success"):
                        tool_result = {
                            "generated_code": generated_code,
                            "execution": execution_result,
                            "generation_model": code_response.model_used,
                        }
                        user_prompt = f"{user_prompt}\n\nCode Execution Output: {json.dumps(tool_result, ensure_ascii=True)}"
                        await self._add_tokens(state=state, tokens=code_response.tokens_used)
                    else:
                        tool_used = None
                        tool_result = None

            if multi_agent_mode:
                coordinator = get_agent_coordinator()
                enriched_step = _build_agent_step_for_execution(step=step, tool_result=tool_result)
                coordinator_result, assigned_agent = await coordinator.execute_step_with_agent(
                    step=enriched_step,
                    state=state,
                )

                selected_agent_name = assigned_agent.name
                selected_agent_role = assigned_agent.role
                routing_reason = _routing_reason_for_step(step=step, agent_role=assigned_agent.role)

                if coordinator_result.status != "success":
                    raise LLMResponseError(coordinator_result.error or "Multi-agent execution failed")

                output_text = (coordinator_result.output or "").strip()
                if not output_text:
                    raise LLMResponseError("Executor received an empty LLM response")

                await self._add_tokens(state=state, tokens=coordinator_result.tokens_used)

                verdict, reason, scores = await self._validate_output(
                    step=step,
                    output=output_text,
                )

                result = coordinator_result.model_copy(
                    update={
                        "step_id": step.step_id,
                        "status": "success",
                        "output": output_text,
                        "tool_used": tool_used,
                        "tool_result": tool_result,
                        "retry_count": retry_count,
                        "validation": verdict,
                        "agent_name": selected_agent_name,
                        "agent_role": selected_agent_role,
                    }
                )

                fallback_transition = None
                preferred_model = str(assigned_agent.preferred_model or "").strip().lower()
                actual_model = str(coordinator_result.model_used or "").strip().lower()
                if preferred_model and actual_model and preferred_model != actual_model:
                    fallback_transition = {
                        "from_provider": _executor_provider_for_model(assigned_agent.preferred_model),
                        "to_provider": _executor_provider_for_model(
                            coordinator_result.model_used or assigned_agent.preferred_model
                        ),
                    }

                agent_assignment = {
                    "agent_name": selected_agent_name,
                    "agent_role": selected_agent_role,
                    "routing_reason": routing_reason or "",
                }
                return result, verdict, reason, scores, fallback_transition, agent_assignment

            llm_response, fallback_used, original_provider = await _call_llm_with_reliability(
                prompt=user_prompt,
                system_prompt=system_prompt,
                task_id=state["task_id"],
                step_id=step.step_id,
            )

            if not llm_response.text.strip():
                raise LLMResponseError("Executor received an empty LLM response")

            await self._add_tokens(state=state, tokens=llm_response.tokens_used)

            verdict, reason, scores = await self._validate_output(
                step=step,
                output=llm_response.text.strip(),
            )

            result = StepResult(
                step_id=step.step_id,
                status="success",
                output=llm_response.text.strip(),
                tokens_used=llm_response.tokens_used,
                latency_ms=max(1, llm_response.latency_ms),
                model_used=llm_response.model_used,
                tool_used=tool_used,
                tool_result=tool_result,
                retry_count=retry_count,
                validation=verdict,
            )

            fallback_transition = None
            if fallback_used:
                fallback_transition = {
                    "from_provider": original_provider,
                    "to_provider": llm_response.provider,
                }

            return result, verdict, reason, scores, fallback_transition, None

        except Exception as exc:
            failure = StepResult(
                step_id=step.step_id,
                status="failed",
                output="",
                tokens_used=0,
                latency_ms=max(1, int((perf_counter() - started) * 1000)),
                model_used="",
                tool_used=tool_used,
                tool_result=tool_result,
                retry_count=retry_count,
                validation="retry",
                error=str(exc),
                agent_name=selected_agent_name,
                agent_role=selected_agent_role,
            )
            assignment = None
            if selected_agent_name and selected_agent_role:
                assignment = {
                    "agent_name": selected_agent_name,
                    "agent_role": selected_agent_role,
                    "routing_reason": routing_reason or "",
                }
            return failure, "retry", str(exc), {}, None, assignment

    async def _validate_output(self, step: StepDefinition, output: str) -> Tuple[str, str, dict[str, int]]:
        """Validate one step output and return (verdict, reason, scores)."""
        try:
            validation_model = _get_validation_model()
            validation_provider = _validator_provider_for_model(validation_model)
            validation_prompt = VALIDATOR_USER_PROMPT_TEMPLATE.format(
                step_name=step.name,
                step_description=step.description,
                step_output=output,
            )
            response = await call_llm(
                prompt=validation_prompt,
                system_prompt=VALIDATOR_SYSTEM_PROMPT,
                model=validation_model,
                provider=validation_provider,
                temperature=0.1,
                max_tokens=1000,
                json_mode=True,
                timeout=45,
            )
            parsed = _parse_validation_response(response.text)
            return (
                str(parsed.get("verdict", "retry")),
                str(parsed.get("reason", "Validator reason missing")),
                parsed.get("scores", {}) or {},
            )
        except (LLMError, ValueError, json.JSONDecodeError):
            fallback = rule_based_validate(output=output, step_description=step.description)
            verdict = str(fallback.get("verdict", "retry"))
            reason = str(fallback.get("reason", "Rule-based validation fallback used"))
            return verdict, reason, {}
        except Exception as exc:  # pragma: no cover - defensive fallback
            fallback = rule_based_validate(output=output, step_description=step.description)
            verdict = str(fallback.get("verdict", "retry"))
            reason = f"Validation fallback due to error: {exc}"
            return verdict, reason, {}

    async def _run_reflection(
        self,
        step: StepDefinition,
        state: AgentState,
        failed_result: StepResult,
        retry_count: int,
        level: int,
        concurrent_ids: List[str],
    ) -> dict[str, Any]:
        """Call reflector for a failed step and translate outcome for parallel execution."""
        reflection_state = create_initial_state(task_id=state["task_id"], user_input=state["original_input"])
        reflection_state["steps"] = [step]
        reflection_state["current_step_index"] = 0
        reflection_state["step_results"] = [failed_result]
        reflection_state["retry_counts"] = {step.step_id: retry_count}
        reflection_state["reflection_counts"] = {
            step.step_id: state.get("reflection_counts", {}).get(step.step_id, 0)
        }
        reflection_state["context_memory"] = list(state.get("context_memory", [])[-5:])
        reflection_state["error_log"] = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step_id": step.step_id,
                "error_type": "PARALLEL_STEP_FAILED",
                "error_message": failed_result.error or "Parallel step failed",
            }
        ]
        reflection_state["status"] = "reflecting"

        reflected = await reflector_node(reflection_state)
        if reflected.get("llm_tokens_used", 0) > 0:
            await self._add_tokens(state=state, tokens=int(reflected["llm_tokens_used"]))

        state["reflection_counts"][step.step_id] = max(
            state["reflection_counts"].get(step.step_id, 0),
            reflected["reflection_counts"].get(step.step_id, 0),
        )

        reflected_trace = []
        for entry in reflected.get("execution_trace", []):
            if not isinstance(entry, dict):
                continue
            normalized = dict(entry)
            normalized["level"] = normalized.get("level", level)
            normalized["concurrent_with"] = normalized.get("concurrent_with") or list(concurrent_ids)
            reflected_trace.append(normalized)

        action = self._extract_reflection_action(reflected_trace)

        for result in reflected.get("step_results", []):
            if result.step_id == step.step_id and result.status == "skipped":
                return {
                    "final_result": result.model_copy(
                        update={
                            "retry_count": retry_count,
                            "validation": "pass",
                        }
                    ),
                    "updated_step": None,
                    "trace_events": reflected_trace,
                    "reason": None,
                }

        if reflected.get("status") == "failed" or action == "ABORT":
            reason = self._extract_reflection_reason(reflected_trace) or failed_result.error or "Reflection requested abort"
            failed = failed_result.model_copy(
                update={
                    "status": "failed",
                    "validation": "reflect",
                    "error": reason,
                }
            )
            return {
                "final_result": failed,
                "updated_step": None,
                "trace_events": reflected_trace,
                "reason": reason,
            }

        reflected_steps = reflected.get("steps", [])
        if len(reflected_steps) > 1:
            decompose_description = self._compose_decomposed_description(reflected_steps)
            updated_step = step.model_copy(update={"description": decompose_description})
            return {
                "final_result": None,
                "updated_step": updated_step,
                "trace_events": reflected_trace,
                "reason": "DECOMPOSE converted to rewritten step description for parallel retry",
            }

        if reflected_steps:
            updated_step = reflected_steps[0]
            if updated_step.description != step.description:
                return {
                    "final_result": None,
                    "updated_step": updated_step,
                    "trace_events": reflected_trace,
                    "reason": None,
                }

        reason = self._extract_reflection_reason(reflected_trace) or failed_result.error or "Reflection could not recover step"
        return {
            "final_result": failed_result.model_copy(
                update={
                    "status": "failed",
                    "validation": "reflect",
                    "error": reason,
                }
            ),
            "updated_step": None,
            "trace_events": reflected_trace,
            "reason": reason,
        }

    @staticmethod
    def _compose_decomposed_description(sub_steps: List[StepDefinition]) -> str:
        """Convert reflected sub-steps into a deterministic rewritten single-step instruction."""
        lines = [
            "Execute the following decomposed sub-steps in order and synthesize a single consolidated output:",
        ]
        for index, sub_step in enumerate(sub_steps, start=1):
            lines.append(f"{index}. {sub_step.name}: {sub_step.description}")
        return "\n".join(lines)

    @staticmethod
    def _extract_reflection_action(trace_events: List[dict[str, Any]]) -> str:
        """Extract reflection action name from reflection_completed trace payload."""
        for entry in reversed(trace_events):
            if str(entry.get("event_type", "")) != "reflection_completed":
                continue
            details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
            action = str(details.get("action", "")).strip().upper()
            if action:
                return action
        return ""

    @staticmethod
    def _extract_reflection_reason(trace_events: List[dict[str, Any]]) -> str:
        """Extract reflection reasoning text from reflection_completed trace payload."""
        for entry in reversed(trace_events):
            if str(entry.get("event_type", "")) != "reflection_completed":
                continue
            details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
            reason = str(details.get("reasoning", "")).strip()
            if reason:
                return reason
        return ""

    @staticmethod
    def _build_context_block(state: AgentState) -> str:
        """Build context snapshot from prior completed levels for concurrent step execution."""
        prior_results = state.get("step_results", [])[-5:]
        if prior_results:
            lines = [
                f"- {result.step_id} ({result.status}): {(result.output or '')[:400]}"
                for result in prior_results
            ]
            context = "\n".join(lines)
        else:
            context = "- No prior results available."

        memory_entries = state.get("context_memory", [])[-5:]
        if memory_entries:
            memory_block = "\n".join(f"- {entry}" for entry in memory_entries)
        else:
            memory_block = "- No accumulated context memory yet."

        return f"{context}\n\nAccumulated Context Memory:\n{memory_block}"

    async def _add_tokens(self, state: AgentState, tokens: int) -> None:
        """Atomically update shared token counters from concurrent tasks."""
        safe_tokens = max(0, int(tokens))
        if safe_tokens == 0:
            return
        async with self._tokens_lock:
            state["llm_tokens_used"] = int(state.get("llm_tokens_used", 0)) + safe_tokens

    async def _append_trace(
        self,
        task_id: str,
        trace_events: List[dict],
        event_type: str,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
        tokens_used: Optional[int] = None,
        model_used: Optional[str] = None,
        error: Optional[str] = None,
        level: Optional[int] = None,
        concurrent_with: Optional[List[str]] = None,
    ) -> None:
        """Append a trace entry locally and publish a matching websocket event."""
        entry = TraceEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            step_id=step_id,
            step_name=step_name,
            details=details or {},
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            model_used=model_used,
            error=error,
            level=level,
            concurrent_with=sorted(concurrent_with or []),
        ).model_dump()
        trace_events.append(entry)

        payload_data = dict(details or {})
        if step_id:
            payload_data["step_id"] = step_id
        if step_name:
            payload_data["step_name"] = step_name
        if duration_ms is not None:
            payload_data["duration_ms"] = duration_ms
        if tokens_used is not None:
            payload_data["tokens_used"] = tokens_used
        if model_used:
            payload_data["model_used"] = model_used
        if error:
            payload_data["error"] = error
        if level is not None:
            payload_data["level"] = level
        if concurrent_with:
            payload_data["concurrent_with"] = sorted(concurrent_with)

        await self._publish_event(task_id=task_id, event_type=event_type, data=payload_data)

    async def _publish_event(self, task_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Best-effort realtime event publication."""
        payload = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "data": data,
        }

        try:
            redis = get_redis_service()
            await redis.publish_event(task_id=task_id, event=payload)
        except Exception as exc:  # pragma: no cover - event publication must be non-fatal
            logger.warning(
                "parallel_event_publish_failed task_id=%s event_type=%s error=%s",
                task_id,
                event_type,
                exc,
            )

    async def _safe_update_step_status(self, task_id: str, step_index: int, status: str) -> None:
        """Best-effort step-status persistence used by UI progress tracking."""
        try:
            redis = get_redis_service()
            await redis.update_step_status(task_id=task_id, step_index=step_index, status=status)
        except Exception as exc:  # pragma: no cover - status updates must be non-fatal
            logger.warning(
                "parallel_step_status_update_failed task_id=%s step_index=%s status=%s error=%s",
                task_id,
                step_index,
                status,
                exc,
            )

    def _mark_task_failed(self, state: AgentState, error_type: str, message: str) -> None:
        """Mark shared state as failed and append standardized error/trace records."""
        state["status"] = "failed"
        now = datetime.now(timezone.utc).isoformat()
        state["error_log"].append(
            {
                "timestamp": now,
                "step_id": "dag",
                "error_type": error_type,
                "error_message": message,
            }
        )
        state["execution_trace"].append(
            TraceEntry(
                timestamp=now,
                event_type="task_failed",
                details={"reason": message, "error_type": error_type},
                error=message,
            ).model_dump()
        )
