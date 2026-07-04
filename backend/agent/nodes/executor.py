"""Executor node that runs the current step using model/tool dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from time import perf_counter
from typing import Any

from agent.multi_agent.coordinator import get_agent_coordinator
from agent.reliability.circuit_breaker import get_circuit_breaker_manager
from agent.reliability.fallback import AllProvidersFailedError, FALLBACK_CHAIN, call_with_fallback
from agent.state import AgentState
from agent.tools import call_api, execute_python_code, search_web
from config import get_settings
from models import StepDefinition, StepResult, TraceEntry
from services.llm_service import LLMResponse, LLMResponseError
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = (
    "You are executing step {step_number} of {total_steps} in a multi-step task. "
    "Use the provided context from previous steps. Be thorough, accurate, and specific. "
    "Do NOT mention that you are an AI or that you cannot access real data - work with what you have."
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

        if step.tool_needed == "web_search":
            tool_used = "web_search"
            tool_result = _safe_web_search(step.description)
            if tool_result.get("success"):
                user_prompt = f"{user_prompt}\n\nSearch Results: {json.dumps(tool_result, ensure_ascii=True)}"
            else:
                logger.warning("executor_tool_failed tool=web_search step_id=%s error=%s", step.step_id, tool_result)
                tool_result = None
                tool_used = None

        elif step.tool_needed == "api_call":
            tool_used = "api_call"
            tool_result = _safe_api_call(step.description)
            if tool_result.get("success"):
                user_prompt = f"{user_prompt}\n\nAPI Response: {json.dumps(tool_result, ensure_ascii=True)}"
            else:
                logger.warning("executor_tool_failed tool=api_call step_id=%s error=%s", step.step_id, tool_result)
                tool_result = None
                tool_used = None

        elif step.tool_needed == "code_exec":
            tool_used = "code_exec"
            code_generation_result = await _safe_generate_code(
                original_input=state["original_input"],
                step_name=step.name,
                step_description=step.description,
                task_id=state["task_id"],
                step_id=step.step_id,
            )
            if code_generation_result is None:
                logger.warning("executor_tool_failed tool=code_exec step_id=%s reason=code_generation_failed", step.step_id)
                tool_result = None
                tool_used = None
            else:
                generated_code = _extract_python_code(code_generation_result.text)
                execution_result = execute_python_code(generated_code)
                if execution_result.get("success"):
                    tool_result = {
                        "generated_code": generated_code,
                        "execution": execution_result,
                        "generation_model": code_generation_result.model_used,
                    }
                    user_prompt = f"{user_prompt}\n\nCode Execution Output: {json.dumps(tool_result, ensure_ascii=True)}"
                    state["llm_tokens_used"] += code_generation_result.tokens_used
                else:
                    logger.warning("executor_tool_failed tool=code_exec step_id=%s error=%s", step.step_id, execution_result)
                    tool_result = None
                    tool_used = None

        if multi_agent_mode:
            coordinator = get_agent_coordinator()
            enriched_step = _build_agent_step_for_execution(step=step, tool_result=tool_result)
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
    if reflection_reason:
        reflection_block = f"- This step was modified by reflector. Reason: {reflection_reason}"
    else:
        reflection_block = "- No reflection-based modification noted for this step."

    return (
        f"{formatted_prior_results}\n\n"
        f"Accumulated Context Memory:\n{memory_block}\n\n"
        f"Reflection Notes:\n{reflection_block}"
    )


def _build_agent_step_for_execution(step: StepDefinition, tool_result: dict[str, Any] | None) -> StepDefinition:
    """Enrich step description with tool output context before multi-agent execution."""
    if not tool_result:
        return step

    try:
        serialized_tool_result = json.dumps(tool_result, ensure_ascii=True)
    except Exception:
        serialized_tool_result = str(tool_result)

    enriched_description = (
        f"{step.description.strip()}\n\n"
        "Tool output context (use this as factual input when relevant):\n"
        f"{serialized_tool_result[:4000]}"
    )
    return step.model_copy(update={"description": enriched_description})


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
    """Extract reflection reason for the current step from memory/error logs."""
    step_pattern = f"Reflection for step {step_id}:"
    for note in reversed(state["context_memory"]):
        if note.startswith(step_pattern):
            return note.replace(step_pattern, "", 1).strip() or "No reason provided"

    for error in reversed(state["error_log"]):
        if str(error.get("step_id") or "") == step_id and "reason" in error:
            return str(error["reason"])

    return None


def _safe_web_search(description: str) -> dict[str, Any]:
    """Run web search tool safely and normalize failures into tool payload shape."""
    try:
        return search_web(query=description, max_results=5)
    except Exception as exc:  # pragma: no cover - defensive tool guard
        return {
            "success": False,
            "data": {},
            "error_message": str(exc),
            "tool_name": "web_search",
        }


def _safe_api_call(description: str) -> dict[str, Any]:
    """Run API caller tool with URL extraction from step description."""
    extracted_url = _extract_url(description)
    if extracted_url is None:
        return {
            "success": False,
            "data": {},
            "error_message": "No URL found in step description for api_call tool.",
            "tool_name": "api_caller",
        }

    try:
        return call_api(method="GET", url=extracted_url)
    except Exception as exc:  # pragma: no cover - defensive tool guard
        return {
            "success": False,
            "data": {},
            "error_message": str(exc),
            "tool_name": "api_caller",
        }


def _extract_url(text: str) -> str | None:
    """Extract the first URL from free-form step text."""
    match = re.search(r"https?://[^\s\]\)\"']+", text)
    return match.group(0) if match else None


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
            "timestamp": datetime.utcnow().isoformat(),
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
            "timestamp": datetime.utcnow().isoformat(),
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
