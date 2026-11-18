"""Executor node that runs the current step using model/tool dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from time import perf_counter
from typing import Any

from agent.multi_agent.coordinator import get_agent_coordinator
from agent.reliability.circuit_breaker import get_circuit_breaker_manager
from agent.reliability.fallback import AllProvidersFailedError, FALLBACK_CHAIN, call_with_fallback
from agent.state import AgentState
from agent.tools.dispatch import ToolOutcome, run_step_tool
from config import get_settings
from models import StepDefinition, StepResult, TraceEntry
from services.llm_service import LLMResponse, LLMResponseError
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = (
    "You are executing step {step_number} of {total_steps} in a multi-step task. "
    "Use the provided context from previous steps. Be thorough, accurate, and specific. "
    "If information you need is missing, or a tool reports a failure, say so explicitly and name what is unknown. "
    "Never invent numbers, names, dates or sources."
)

USER_PROMPT_TEMPLATE = """Overall Task: {original_input}

Current Step ({step_id}): {step_name}
Instructions: {step_description}

Context from Previous Steps:
{formatted_prior_results}

Execute this step now. Provide a complete, detailed result."""


async def executor_node(state: AgentState) -> AgentState:
    """Execute the current step using LLM + optional tools."""
    state["status"] = "executing"

    if state["current_step_index"] >= len(state["steps"]):
        return state

    started = perf_counter()
    step = state["steps"][state["current_step_index"]]
    retry_count = state["retry_counts"].get(step.step_id, 0)
    prompt_length = 0
    system_prompt = ""
    user_prompt = ""
    runtime_settings = get_settings()
    multi_agent_mode = bool(getattr(runtime_settings, "MULTI_AGENT_MODE", True))
    selected_agent_name: str | None = None
    selected_agent_role: str | None = None

    if retry_count > 0:
        retry_reason = _last_step_error_reason(state=state, step_id=step.step_id)
        await _publish_retry_event(
            task_id=state["task_id"],
            step_id=step.step_id,
            attempt=retry_count,
            reason=retry_reason,
        )

    try:
        context_block = _build_context_block(state=state, step_id=step.step_id)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            step_number=state["current_step_index"] + 1,
            total_steps=len(state["steps"]),
        )
        user_prompt = USER_PROMPT_TEMPLATE.format(
            original_input=state["original_input"],
            step_id=step.step_id,
            step_name=step.name,
            step_description=step.description,
            formatted_prior_results=context_block,
        )
        prompt_length = len(system_prompt) + len(user_prompt)

        tool_used: str | None = None
        tool_result: dict[str, Any] | None = None
        llm_response: LLMResponse | None = None
        fallback_used = False
        original_provider = _provider_for_model(runtime_settings.primary_model or "meta-llama/Llama-3.1-8B-Instruct")

        if step.tool_needed == "none" and not multi_agent_mode:
            output = step.description.strip() or step.name
            if not output:
                output = "No explicit output provided for pass-through step."

            latency_ms = int((perf_counter() - started) * 1000)
            result = StepResult(
                step_id=step.step_id,
                status="success",
                output=output,
                tokens_used=0,
                latency_ms=latency_ms,
                model_used="",
                tool_used=None,
                tool_result=None,
                retry_count=retry_count,
            )
            _record_success(
                state=state,
                result=result,
                prompt_length=prompt_length,
                tool_used=None,
                retry_count=retry_count,
                fallback_used=False,
            )
            return state

        tool_outcome = await run_step_tool(step, generate_code=_code_generator(state))
        if tool_outcome.tool is not None:
            tool_used = tool_outcome.tool
            tool_result = _tool_result_payload(tool_outcome)
            user_prompt = f"{user_prompt}\n\n{tool_outcome.prompt_block}"
            state["llm_tokens_used"] += tool_outcome.tokens_used
            if tool_outcome.failed:
                _record_tool_failure(state=state, step=step, outcome=tool_outcome)

        if multi_agent_mode:
            coordinator = get_agent_coordinator()
            enriched_step = _build_agent_step_for_execution(step=step, tool_block=tool_outcome.prompt_block)
            coordinator_result, assigned_agent = await coordinator.execute_step_with_agent(
                step=enriched_step,
                state=state,
            )

            selected_agent_name = assigned_agent.name
            selected_agent_role = assigned_agent.role

            if coordinator_result.status != "success":
                raise LLMResponseError(coordinator_result.error or "Multi-agent execution failed")

            if not coordinator_result.output.strip():
                raise LLMResponseError("Executor received an empty LLM response")

            llm_response = LLMResponse(
                text=coordinator_result.output,
                tokens_used=coordinator_result.tokens_used,
                latency_ms=coordinator_result.latency_ms,
                model_used=coordinator_result.model_used,
                provider=_provider_for_model(coordinator_result.model_used or assigned_agent.preferred_model),
            )
            original_provider = _provider_for_model(assigned_agent.preferred_model)
            fallback_used = _is_model_fallback_used(
                actual_model=coordinator_result.model_used,
                preferred_model=assigned_agent.preferred_model,
            )

            await _append_agent_assigned_trace(
                state=state,
                step=step,
                agent_name=assigned_agent.name,
                agent_role=assigned_agent.role,
                routing_reason=_routing_reason_for_step(step=step, agent_role=assigned_agent.role),
            )

            result = coordinator_result.model_copy(
                update={
                    "step_id": step.step_id,
                    "tool_used": tool_used,
                    "tool_result": tool_result,
                    "retry_count": retry_count,
                    "agent_name": assigned_agent.name,
                    "agent_role": assigned_agent.role,
                }
            )
        else:
            llm_response, fallback_used, original_provider = await _call_llm_with_reliability(
                prompt=user_prompt,
                system_prompt=system_prompt,
                task_id=state["task_id"],
                step_id=step.step_id,
            )

            if not llm_response.text.strip():
                raise LLMResponseError("Executor received an empty LLM response")

            result = StepResult(
                step_id=step.step_id,
                status="success",
                output=llm_response.text.strip(),
                tokens_used=llm_response.tokens_used,
                latency_ms=llm_response.latency_ms,
                model_used=llm_response.model_used,
                tool_used=tool_used,
                tool_result=tool_result,
                retry_count=retry_count,
            )

        if fallback_used:
            await _publish_fallback_event(
                task_id=state["task_id"],
                step_id=step.step_id,
                from_provider=original_provider,
                to_provider=llm_response.provider,
            )

        state["llm_tokens_used"] += llm_response.tokens_used
        _record_success(
            state=state,
            result=result,
            prompt_length=prompt_length,
            tool_used=tool_used,
            retry_count=retry_count,
            fallback_used=fallback_used,
        )
        return state

    except AllProvidersFailedError as exc:
        logger.warning(
            "executor_all_providers_failed task_id=%s step_id=%s errors=%s",
            state["task_id"],
            step.step_id,
            getattr(exc, "errors", []),
        )
        chaos_metadata = _chaos_metadata_from_provider_errors(getattr(exc, "errors", []))
        failed_result = StepResult(
            step_id=step.step_id,
            status="failed",
            output="",
            tokens_used=0,
            latency_ms=int((perf_counter() - started) * 1000),
            model_used="",
            tool_used=step.tool_needed if step.tool_needed != "none" else None,
            tool_result=None,
            retry_count=retry_count,
            error="All LLM providers unavailable",
            agent_name=selected_agent_name,
            agent_role=selected_agent_role,
        )
        state["step_results"].append(failed_result)
        trace_details = {
            "input_prompt_length": prompt_length,
            "output_length": 0,
            "tool_used": step.tool_needed,
            "retry_count": retry_count,
            "fallback_used": True,
            "model_used": "",
            "agent_name": selected_agent_name,
            "agent_role": selected_agent_role,
        }
        trace_details.update(chaos_metadata)
        state["execution_trace"].append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="step_failed",
                step_id=step.step_id,
                step_name=step.name,
                details=trace_details,
                duration_ms=failed_result.latency_ms,
                tokens_used=0,
                model_used="",
                error="All LLM providers unavailable",
            ).model_dump()
        )
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step.step_id,
            "error_type": "ALL_PROVIDERS_FAILED",
            "error_message": "All LLM providers unavailable",
            "provider_errors": getattr(exc, "errors", []),
        }
        error_entry.update(chaos_metadata)
        state["error_log"].append(
            error_entry
        )
        return state

    except Exception as exc:
        logger.warning("executor_step_failed task_id=%s step_id=%s error=%s", state["task_id"], step.step_id, exc)
        chaos_metadata = _chaos_metadata_from_exception(exc)
        failed_result = StepResult(
            step_id=step.step_id,
            status="failed",
            output="",
            tokens_used=0,
            latency_ms=int((perf_counter() - started) * 1000),
            model_used="",
            tool_used=step.tool_needed if step.tool_needed != "none" else None,
            tool_result=None,
            retry_count=retry_count,
            error=str(exc),
            agent_name=selected_agent_name,
            agent_role=selected_agent_role,
        )
        state["step_results"].append(failed_result)
        trace_details = {
            "input_prompt_length": prompt_length,
            "output_length": 0,
            "tool_used": step.tool_needed,
            "retry_count": retry_count,
            "fallback_used": False,
            "model_used": "",
            "agent_name": selected_agent_name,
            "agent_role": selected_agent_role,
        }
        trace_details.update(chaos_metadata)
        state["execution_trace"].append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="step_failed",
                step_id=step.step_id,
                step_name=step.name,
                details=trace_details,
                duration_ms=failed_result.latency_ms,
                tokens_used=0,
                model_used="",
                error=str(exc),
            ).model_dump()
        )
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step.step_id,
            "error_type": "EXECUTION_FAILURE",
            "error_message": str(exc),
        }
        error_entry.update(chaos_metadata)
        state["error_log"].append(
            error_entry
        )
        return state


def _build_context_block(state: AgentState, step_id: str) -> str:
    """Build ordered execution context from prior results, memory, and reflection notes."""
    prior_results = state["step_results"][-3:]
    if prior_results:
        prior_lines = []
        for result in prior_results:
            prior_lines.append(
                f"- {result.step_id} ({result.status}): {(result.output or '')[:500]}"
            )
        formatted_prior_results = "\n".join(prior_lines)
    else:
        formatted_prior_results = "- No prior results available."

    memory_entries = state["context_memory"][-3:]
    if memory_entries:
        memory_block = "\n".join(f"- {entry}" for entry in memory_entries)
    else:
        memory_block = "- No accumulated context memory yet."

    reflection_reason = _extract_reflection_reason(state=state, step_id=step_id)
    feedback = _latest_validator_feedback(state=state, step_id=step_id)
    notes: list[str] = []
    if reflection_reason:
        notes.append(f"- The reflector changed this step: {reflection_reason}")
    if feedback:
        notes.append(f"- Reviewer feedback on the previous attempt (fix this): {feedback}")
    reflection_block = "\n".join(notes) if notes else "- No earlier attempt for this step."

    return (
        f"{formatted_prior_results}\n\n"
        f"Accumulated Context Memory:\n{memory_block}\n\n"
        f"Notes for this attempt:\n{reflection_block}"
    )


def _build_agent_step_for_execution(step: StepDefinition, tool_block: str | None) -> StepDefinition:
    """Append the fenced tool output (or the tool-failure note) to the step for agent execution."""
    if not tool_block:
        return step
    return step.model_copy(update={"description": f"{step.description.strip()}\n\n{tool_block}"})


def _tool_result_payload(outcome: ToolOutcome) -> dict[str, Any]:
    """Serializable record of a tool call for step results and traces."""
    return {"ok": outcome.ok, "error": outcome.error, "payload": outcome.payload}


def _record_tool_failure(state: AgentState, step: StepDefinition, outcome: ToolOutcome) -> None:
    """Record a failed tool call so traces and metrics show it instead of hiding it."""
    timestamp = datetime.now(timezone.utc).isoformat()
    state["error_log"].append(
        {
            "timestamp": timestamp,
            "step_id": step.step_id,
            "error_type": "TOOL_FAILURE",
            "tool": outcome.tool,
            "error_message": outcome.error or "tool failed",
        }
    )
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="tool_failed",
            step_id=step.step_id,
            step_name=step.name,
            details={"tool": outcome.tool},
            error=outcome.error,
        ).model_dump()
    )


def _code_generator(state: AgentState):
    """Return an async callable that asks the model for Python code for a step."""

    async def _generate(step: StepDefinition) -> tuple[str, int, str] | None:
        response = await _safe_generate_code(
            original_input=state["original_input"],
            step_name=step.name,
            step_description=step.description,
            task_id=state["task_id"],
            step_id=step.step_id,
        )
        if response is None:
            return None
        return _extract_python_code(response.text), response.tokens_used, response.model_used

    return _generate


async def _append_agent_assigned_trace(
    state: AgentState,
    step: StepDefinition,
    agent_name: str,
    agent_role: str,
    routing_reason: str,
) -> None:
    """Append and publish agent assignment event for observability consumers."""
    timestamp = datetime.now(timezone.utc).isoformat()
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="agent_assigned",
            step_id=step.step_id,
            step_name=step.name,
            agent_name=agent_name,
            details={
                "agent_name": agent_name,
                "agent_role": agent_role,
                "routing_reason": routing_reason,
            },
        ).model_dump()
    )

    await _publish_event(
        task_id=state["task_id"],
        event={
            "event_type": "agent_assigned",
            "timestamp": timestamp,
            "step_id": step.step_id,
            "agent_name": agent_name,
            "agent_role": agent_role,
            "routing_reason": routing_reason,
        },
    )


def _routing_reason_for_step(step: StepDefinition, agent_role: str) -> str:
    """Return compact routing rationale for trace events."""
    tool_needed = str(step.tool_needed or "").strip().lower()
    if tool_needed in {"web_search", "api_call", "code_exec"}:
        return f"tool_based:{tool_needed}"
    return f"semantic_classification:{agent_role}"


def _is_model_fallback_used(actual_model: str, preferred_model: str) -> bool:
    """Detect provider fallback by comparing preferred and actual model names."""
    return (actual_model or "").strip().lower() != (preferred_model or "").strip().lower()


def _extract_reflection_reason(state: AgentState, step_id: str) -> str | None:
    """Return the reflector's note for this step, if it modified it."""
    for prefix in (f"Reflection for step {step_id}:", f"Reflection modified {step_id}:"):
        for note in reversed(state["context_memory"]):
            if note.startswith(prefix):
                return note.replace(prefix, "", 1).strip() or "No reason provided"
    return None


def _latest_validator_feedback(state: AgentState, step_id: str) -> str | None:
    """Return the most recent non-passing validator reason for this step."""
    for entry in reversed(state.get("validation_log") or []):
        if str(entry.get("step_id") or "") != step_id:
            continue
        if entry.get("verdict") == "pass" or entry.get("validator_verdict") == "pass":
            return None
        return str(entry.get("reason") or "") or None
    return None


async def _safe_generate_code(
    original_input: str,
    step_name: str,
    step_description: str,
    task_id: str,
    step_id: str,
) -> LLMResponse | None:
    """Request executable Python code for code_exec steps."""
    code_system_prompt = (
        "You generate Python code for sandbox execution. "
        "Return only Python code without explanations or markdown fences."
    )
    code_user_prompt = (
        "Overall Task:\n"
        f"{original_input}\n\n"
        "Current Step:\n"
        f"{step_name}\n"
        f"{step_description}\n\n"
        "Generate Python code that executes this step and prints the final result."
    )

    try:
        response, _, _ = await _call_llm_with_reliability(
            prompt=code_user_prompt,
            system_prompt=code_system_prompt,
            task_id=task_id,
            step_id=step_id,
            max_tokens=2000,
            temperature=0.1,
        )
        return response
    except Exception:
        return None


def _extract_python_code(text: str) -> str:
    """Extract Python code from fenced or plain model output."""
    stripped = text.strip()
    fenced = re.search(r"```(?:python)?\s*(.*?)```", stripped, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return stripped


async def _call_llm_with_reliability(
    prompt: str,
    system_prompt: str,
    task_id: str,
    step_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: int = 60,
) -> tuple[LLMResponse, bool, str]:
    """Call LLM via reliability fallback chain + circuit breaker manager."""
    settings = get_settings()
    primary_model = settings.primary_model or "meta-llama/Llama-3.1-8B-Instruct"
    original_provider = _provider_for_model(primary_model)

    runtime_fallback_chain = _build_runtime_fallback_chain(primary_model)
    circuit_breaker = get_circuit_breaker_manager()

    response = await call_with_fallback(
        prompt=prompt,
        system_prompt=system_prompt,
        json_mode=False,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        task_id=task_id,
        step_id=step_id,
        fallback_chain=runtime_fallback_chain,
        circuit_breaker=circuit_breaker,
    )

    fallback_used = _is_fallback_used(
        response=response,
        primary_model=primary_model,
        primary_provider=original_provider,
    )
    return response, fallback_used, original_provider


def _build_runtime_fallback_chain(primary_model: str) -> list[dict[str, str]]:
    """Create a runtime fallback chain preserving configured primary model first."""
    chain: list[dict[str, str]] = [
        {
            "provider": _provider_for_model(primary_model),
            "model": primary_model,
            "label": "Configured Primary",
        }
    ]

    seen_pairs = {(chain[0]["provider"], chain[0]["model"])}
    for entry in FALLBACK_CHAIN:
        provider = str(entry.get("provider", "")).strip().lower()
        model = str(entry.get("model", "")).strip()
        label = str(entry.get("label", f"{provider}/{model}")).strip()
        pair = (provider, model)

        if not provider or not model:
            continue
        if pair in seen_pairs:
            continue

        chain.append(
            {
                "provider": provider,
                "model": model,
                "label": label,
            }
        )
        seen_pairs.add(pair)

    return chain


def _is_fallback_used(response: LLMResponse, primary_model: str, primary_provider: str) -> bool:
    """Determine if fallback path was used based on actual provider/model returned."""
    actual_model = (response.model_used or "").strip().lower()
    actual_provider = (response.provider or "").strip().lower()
    expected_model = primary_model.strip().lower()
    expected_provider = primary_provider.strip().lower()
    return actual_model != expected_model or actual_provider != expected_provider


def _provider_for_model(model_name: str) -> str:
    """Infer provider from model name for fallback chains."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gpt"):
        return "openai"
    return "open_source"


def _record_success(
    state: AgentState,
    result: StepResult,
    prompt_length: int,
    tool_used: str | None,
    retry_count: int,
    fallback_used: bool,
) -> None:
    """Apply success-state mutations for step completion."""
    state["step_results"].append(result)
    takeaway = (result.output or "")[:200]
    if takeaway:
        state["context_memory"].append(takeaway)

    state["execution_trace"].append(
        TraceEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="step_completed",
            step_id=result.step_id,
            details={
                "input_prompt_length": prompt_length,
                "output_length": len(result.output or ""),
                "tool_used": tool_used,
                "retry_count": retry_count,
                "fallback_used": fallback_used,
                "model_used": result.model_used,
                "agent_name": result.agent_name,
                "agent_role": result.agent_role,
            },
            duration_ms=result.latency_ms,
            tokens_used=result.tokens_used,
            model_used=result.model_used,
            agent_name=result.agent_name,
        ).model_dump()
    )


def _last_step_error_reason(state: AgentState, step_id: str) -> str:
    """Extract the most recent error reason associated with a step."""
    for entry in reversed(state["error_log"]):
        if str(entry.get("step_id", "")) != step_id:
            continue
        if entry.get("error_message"):
            return str(entry["error_message"])
        if entry.get("reason"):
            return str(entry["reason"])
        if entry.get("error"):
            return str(entry["error"])
    return "Retry triggered after previous failure"


async def _publish_retry_event(task_id: str, step_id: str, attempt: int, reason: str) -> None:
    """Publish retry event for observability consumers via Redis channel."""
    await _publish_event(
        task_id=task_id,
        event={
            "event_type": "retry_triggered",
            "step_id": step_id,
            "attempt": attempt,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _publish_fallback_event(
    task_id: str,
    step_id: str,
    from_provider: str,
    to_provider: str,
) -> None:
    """Publish provider fallback transition event to Redis."""
    await _publish_event(
        task_id=task_id,
        event={
            "event_type": "fallback_triggered",
            "step_id": step_id,
            "from_provider": from_provider,
            "to_provider": to_provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _publish_event(task_id: str, event: dict[str, Any]) -> None:
    """Best-effort Redis event publication that never breaks execution flow."""
    try:
        redis_service = get_redis_service()
        await redis_service.publish_event(task_id, event)
    except Exception as exc:  # pragma: no cover - observability must not break runtime
        logger.warning(
            "executor_event_publish_failed task_id=%s event_type=%s error=%s",
            task_id,
            event.get("event_type"),
            exc,
        )


def _chaos_metadata_from_provider_errors(provider_errors: Any) -> dict[str, Any]:
    """Extract chaos metadata from AllProvidersFailedError provider error payloads."""
    if not isinstance(provider_errors, list):
        return {}

    for entry in provider_errors:
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("chaos_injected")):
            continue

        injection_type = str(entry.get("injection_type", "")).strip().lower() or "unknown"
        return {
            "chaos_injected": True,
            "injection_type": injection_type,
        }

    return {}


def _chaos_metadata_from_exception(error: Exception) -> dict[str, Any]:
    """Extract chaos metadata directly from exception attributes."""
    if not bool(getattr(error, "chaos_injected", False)):
        return {}

    injection_type = str(getattr(error, "injection_type", "")).strip().lower() or "unknown"
    return {
        "chaos_injected": True,
        "injection_type": injection_type,
    }
