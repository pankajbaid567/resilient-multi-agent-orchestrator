"""Reflector node implementing self-healing strategy selection for failed steps."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from agent.state import AgentState
from config import get_settings
from models import StepDefinition, StepResult, TraceEntry
from services.llm_service import LLMError, call_llm

logger = logging.getLogger(__name__)

REFLECTOR_SYSTEM_PROMPT = (
    "You are a senior debugging engineer. A step in an automated task has failed multiple times. "
    "Analyze the failure and recommend a recovery strategy."
)

VALID_ACTIONS = {"MODIFY_STEP", "SKIP_STEP", "DECOMPOSE", "ABORT"}


async def reflector_node(state: AgentState) -> AgentState:
    """Analyze failed step and decide recovery: MODIFY_STEP, SKIP_STEP, DECOMPOSE, or ABORT."""
    state["status"] = "reflecting"

    current_index = state.get("current_step_index", 0)
    if current_index < 0 or current_index >= len(state.get("steps", [])):
        return state

    failed_step = state["steps"][current_index]
    step_id = failed_step.step_id

    failed_results = _collect_failed_results(state=state, step_id=step_id)
    related_errors = _collect_related_errors(state=state, step_id=step_id)
    successful_results = _collect_prior_successes(state=state, current_step_id=step_id)
    reflection_count = state["reflection_counts"].get(step_id, 0)

    now = datetime.now(timezone.utc).isoformat()
    state["execution_trace"].append(
        TraceEntry(
            timestamp=now,
            event_type="reflection_started",
            step_id=step_id,
            step_name=failed_step.name,
            details={
                "failed_attempts": len(failed_results),
                "previous_reflections": reflection_count,
            },
        ).model_dump()
    )

    # Count this invocation so global/step-level guardrails include the current reflection.
    state["reflection_counts"][step_id] = reflection_count + 1
    total_reflections = sum(state["reflection_counts"].values())

    decision: dict[str, Any]
    reflection_model = ""
    reflection_tokens = 0

    forced_action: str | None = None
    forced_reason: str | None = None

    if total_reflections >= 5:
        forced_action = "ABORT"
        forced_reason = "Forced ABORT because total reflections across all steps reached the limit (>= 5)."
    elif reflection_count >= 2:
        forced_action = "SKIP_STEP"
        forced_reason = "Forced SKIP_STEP because this step has already been reflected on 2+ times."

    if forced_action is not None:
        decision = {
            "action": forced_action,
            "reasoning": forced_reason,
            "modified_step": "",
            "sub_steps": [],
            "partial_result": _default_partial_result(failed_results, failed_step.description),
        }
    else:
        prompt = _build_reflection_prompt(
            state=state,
            failed_step=failed_step,
            failed_results=failed_results,
            related_errors=related_errors,
            successful_results=successful_results,
            reflection_count=reflection_count,
        )
        decision, reflection_model, reflection_tokens = await _get_reflection_decision(prompt)
        if reflection_tokens > 0:
            state["llm_tokens_used"] += reflection_tokens

    action = _normalize_action(str(decision.get("action", "")))
    if action not in VALID_ACTIONS:
        action = "SKIP_STEP"
        decision["reasoning"] = (
            f"Invalid action from reflection output. Defaulted to SKIP_STEP. "
            f"Original action={decision.get('action', '')}"
        )

    if action == "DECOMPOSE":
        sub_steps = decision.get("sub_steps") or []
        if not isinstance(sub_steps, list):
            sub_steps = []
        if len(sub_steps) > 3:
            sub_steps = sub_steps[:3]
            decision["sub_steps"] = sub_steps

    try:
        if action == "MODIFY_STEP":
            _apply_modify_step(state=state, step_index=current_index, decision=decision)
        elif action == "SKIP_STEP":
            _apply_skip_step(state=state, step_index=current_index, decision=decision, model_used=reflection_model)
        elif action == "DECOMPOSE":
            decomposed = _apply_decompose(state=state, step_index=current_index, decision=decision)
            if not decomposed:
                action = "SKIP_STEP"
                decision["reasoning"] = (
                    "DECOMPOSE selected but no valid sub-steps were produced. Defaulted to SKIP_STEP."
                )
                _apply_skip_step(state=state, step_index=current_index, decision=decision, model_used=reflection_model)
        else:
            _apply_abort(state=state, step_index=current_index, decision=decision)
    except Exception as exc:  # pragma: no cover - defensive no-raise safety
        logger.exception("reflector_action_application_failed task_id=%s step_id=%s error=%s", state.get("task_id"), step_id, exc)
        action = "ABORT"
        decision["reasoning"] = f"Reflection action handler failed; forced ABORT. Error: {exc}"
        _apply_abort(state=state, step_index=current_index, decision=decision)

    _append_reflection_completed_trace(
        state=state,
        step_index=min(current_index, len(state["steps"]) - 1) if state["steps"] else current_index,
        action=action,
        decision=decision,
        model_used=reflection_model,
        tokens_used=reflection_tokens,
    )

    if action != "ABORT":
        state["status"] = "executing"

    logger.info(
        "reflector_completed task_id=%s step_id=%s action=%s reflections_step=%s total_reflections=%s",
        state.get("task_id"),
        step_id,
        action,
        state["reflection_counts"].get(step_id, 0),
        sum(state["reflection_counts"].values()),
    )
    return state


def _collect_failed_results(state: AgentState, step_id: str) -> list[StepResult]:
    """Collect all failed results for the target step, preserving historical retries."""
    failed = [result for result in state["step_results"] if result.step_id == step_id and result.status == "failed"]
    if failed:
        return failed
    return [result for result in state["step_results"] if result.step_id == step_id]


def _collect_related_errors(state: AgentState, step_id: str) -> list[dict[str, Any]]:
    """Collect error-log entries related to a specific step."""
    related: list[dict[str, Any]] = []
    for entry in state["error_log"]:
        if str(entry.get("step_id", "")) == step_id:
            related.append(entry)
    return related


def _collect_prior_successes(state: AgentState, current_step_id: str) -> list[StepResult]:
    """Collect successful step results used as prior context for reflection."""
    return [
        result
        for result in state["step_results"]
        if result.status == "success" and result.step_id != current_step_id
    ]


def _build_reflection_prompt(
    state: AgentState,
    failed_step: StepDefinition,
    failed_results: list[StepResult],
    related_errors: list[dict[str, Any]],
    successful_results: list[StepResult],
    reflection_count: int,
) -> str:
    """Build full reflection prompt containing failure details and available recovery strategies."""
    last_result = failed_results[-1] if failed_results else None
    last_output = "EMPTY"
    last_error = "No explicit error"

    if last_result is not None:
        if (last_result.output or "").strip():
            last_output = (last_result.output or "")[:500]
        if (last_result.error or "").strip():
            last_error = str(last_result.error)

    error_types = [str(entry.get("error_type", "UNKNOWN")) for entry in related_errors]
    completed_context = [
        f"{result.step_id}: {(result.output or '')[:100]}"
        for result in successful_results
    ]

    return f"""## Failed Task Context
Original Task: {state['original_input']}

## Failed Step
Step ID: {failed_step.step_id}
Name: {failed_step.name}
Description: {failed_step.description}
Tool: {failed_step.tool_needed}

## Failure Details
Attempts: {len(failed_results)}
Last Output: {last_output}
Last Error: {last_error}
Error Types: {error_types}

## Prior Context
Completed Steps: {completed_context}

## Reflection History
Previous reflections on this step: {reflection_count}

## Available Strategies
1. MODIFY_STEP: Rewrite the step description for better results. Use when the step's instructions were ambiguous, too broad, or led to a wrong approach.
2. SKIP_STEP: Mark step as skipped with a partial result. Use when the step is non-critical or when partial data from failed attempts is sufficient.
3. DECOMPOSE: Break this step into 2-3 smaller, more focused sub-steps. Use when the step is too complex for a single execution.
4. ABORT: Stop the entire task. Use ONLY when the task fundamentally cannot be completed (e.g., requires unavailable external access).

Choose ONE strategy. Respond with JSON:
{{
  'action': 'MODIFY_STEP|SKIP_STEP|DECOMPOSE|ABORT',
  'reasoning': 'Detailed explanation of why this strategy was chosen',
  'modified_step': 'New step description (for MODIFY_STEP only, empty otherwise)',
  'sub_steps': [{{'name': '...', 'description': '...', 'tool_needed': '...'}}],
  'partial_result': 'Best available partial result (for SKIP_STEP only)'
}}"""


async def _get_reflection_decision(prompt: str) -> tuple[dict[str, Any], str, int]:
    """Call LLM for reflection and return parsed decision + model metadata."""
    settings = get_settings()
    model_name = settings.primary_model or "meta-llama/Llama-3.1-8B-Instruct"
    provider = _infer_provider(model_name)

    try:
        response = await call_llm(
            prompt=prompt,
            system_prompt=REFLECTOR_SYSTEM_PROMPT,
            model=model_name,
            provider=provider,
            temperature=0.2,
            max_tokens=1200,
            json_mode=True,
            timeout=60,
        )
        parsed = _parse_reflection_response(response.text)
        return parsed, response.model_used, response.tokens_used
    except LLMError as exc:
        logger.warning("reflector_llm_error error=%s", exc)
    except Exception as exc:  # pragma: no cover - defensive parser/transport guard
        logger.warning("reflector_unexpected_error error=%s", exc)

    return {
        "action": "SKIP_STEP",
        "reasoning": "Reflection model unavailable; defaulted to SKIP_STEP.",
        "modified_step": "",
        "sub_steps": [],
        "partial_result": "",
    }, "", 0


def _parse_reflection_response(raw_text: str) -> dict[str, Any]:
    """Parse reflection response via JSON, then heuristic extraction if parsing fails."""
    payload = _extract_json_payload(raw_text)
    candidates = [payload, raw_text]

    for candidate in candidates:
        if not candidate.strip():
            continue

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return _normalize_decision_dict(parsed)
        except json.JSONDecodeError:
            pass

        try:
            parsed_literal = ast.literal_eval(candidate)
            if isinstance(parsed_literal, dict):
                return _normalize_decision_dict(parsed_literal)
        except (ValueError, SyntaxError):
            continue

    heuristic_action = _heuristic_action(raw_text)
    if heuristic_action is None:
        heuristic_action = "SKIP_STEP"

    return {
        "action": heuristic_action,
        "reasoning": "Used heuristic action extraction because reflection response was not valid JSON.",
        "modified_step": "",
        "sub_steps": [],
        "partial_result": "",
    }


def _extract_json_payload(text: str) -> str:
    """Extract JSON-like payload from plain or fenced text."""
    stripped = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _normalize_decision_dict(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize parsed reflection JSON into expected decision schema."""
    sub_steps_raw = parsed.get("sub_steps")
    sub_steps: list[dict[str, str]] = []

    if isinstance(sub_steps_raw, list):
        for entry in sub_steps_raw:
            if not isinstance(entry, dict):
                continue
            sub_steps.append(
                {
                    "name": str(entry.get("name", "")).strip(),
                    "description": str(entry.get("description", "")).strip(),
                    "tool_needed": _normalize_tool_needed(entry.get("tool_needed")),
                }
            )

    return {
        "action": _normalize_action(str(parsed.get("action", ""))),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
        "modified_step": str(parsed.get("modified_step", "")).strip(),
        "sub_steps": sub_steps,
        "partial_result": str(parsed.get("partial_result", "")).strip(),
    }


def _heuristic_action(raw_text: str) -> str | None:
    """Heuristically infer action from unstructured model output."""
    text = (raw_text or "").upper()

    patterns = [
        (r"\bMODIFY_STEP\b|\bMODIFY STEP\b|\bMODIFY\b", "MODIFY_STEP"),
        (r"\bSKIP_STEP\b|\bSKIP STEP\b|\bSKIP\b", "SKIP_STEP"),
        (r"\bDECOMPOSE\b|\bBREAK\s+.*\s+SUB[-\s]?STEPS?\b", "DECOMPOSE"),
        (r"\bABORT\b|\bSTOP\s+THE\s+ENTIRE\s+TASK\b", "ABORT"),
    ]

    for pattern, action in patterns:
        if re.search(pattern, text):
            return action
    return None


def _normalize_action(action: str) -> str:
    """Normalize action values into canonical uppercase strategy names."""
    normalized = action.strip().upper().replace(" ", "_")
    if normalized == "MODIFY":
        return "MODIFY_STEP"
    if normalized == "SKIP":
        return "SKIP_STEP"
    return normalized


def _apply_modify_step(state: AgentState, step_index: int, decision: dict[str, Any]) -> None:
    """Handle MODIFY_STEP by updating step text and resetting retry count."""
    step = state["steps"][step_index]
    updated_description = str(decision.get("modified_step", "")).strip()
    if not updated_description:
        updated_description = f"{step.description.strip()}\n\nRefinement hint: make the approach more explicit and narrow."

    state["steps"][step_index] = step.model_copy(update={"description": updated_description})
    state["retry_counts"][step.step_id] = 0
    state["context_memory"].append(f"Reflection modified {step.step_id}: {updated_description[:180]}")


def _apply_skip_step(state: AgentState, step_index: int, decision: dict[str, Any], model_used: str) -> None:
    """Handle SKIP_STEP by recording a skipped result and moving to the next step."""
    step = state["steps"][step_index]
    partial_result = str(decision.get("partial_result", "")).strip()
    if not partial_result:
        failed_results = _collect_failed_results(state=state, step_id=step.step_id)
        partial_result = _default_partial_result(failed_results, step.description)

    state["step_results"].append(
        StepResult(
            step_id=step.step_id,
            status="skipped",
            output=partial_result,
            tokens_used=0,
            latency_ms=0,
            model_used=model_used,
            tool_used=step.tool_needed if step.tool_needed != "none" else None,
            tool_result=None,
            retry_count=state["retry_counts"].get(step.step_id, 0),
            validation="pass",
            error=None,
        )
    )

    state["context_memory"].append(f"Reflection skipped {step.step_id}: {partial_result[:180]}")
    state["current_step_index"] += 1


def _apply_decompose(state: AgentState, step_index: int, decision: dict[str, Any]) -> bool:
    """Handle DECOMPOSE by replacing failed step with up to three focused sub-steps."""
    failed_step = state["steps"][step_index]
    sub_steps_raw = decision.get("sub_steps")
    if not isinstance(sub_steps_raw, list) or not sub_steps_raw:
        return False

    sub_steps_raw = sub_steps_raw[:3]
    created_sub_steps = _create_sub_steps(
        existing_steps=state["steps"],
        failed_step=failed_step,
        sub_steps_raw=sub_steps_raw,
    )
    if not created_sub_steps:
        return False

    previous_steps = state["steps"]
    replacement_last_id = created_sub_steps[-1].step_id
    updated_steps = previous_steps[:step_index] + created_sub_steps + previous_steps[step_index + 1 :]

    for idx in range(step_index + len(created_sub_steps), len(updated_steps)):
        existing = updated_steps[idx]
        if failed_step.step_id not in existing.dependencies:
            continue

        new_dependencies: list[str] = []
        for dependency in existing.dependencies:
            mapped_dependency = replacement_last_id if dependency == failed_step.step_id else dependency
            if mapped_dependency != existing.step_id and mapped_dependency not in new_dependencies:
                new_dependencies.append(mapped_dependency)

        updated_steps[idx] = existing.model_copy(update={"dependencies": new_dependencies})

    state["steps"] = updated_steps
    state["current_step_index"] = step_index
    state["context_memory"].append(
        f"Reflection decomposed {failed_step.step_id} into {[step.step_id for step in created_sub_steps]}"
    )
    return True


def _create_sub_steps(
    existing_steps: list[StepDefinition],
    failed_step: StepDefinition,
    sub_steps_raw: list[dict[str, Any]],
) -> list[StepDefinition]:
    """Create validated StepDefinition objects for DECOMPOSE action."""
    max_step_number = 0
    for step in existing_steps:
        match = re.fullmatch(r"step_(\d+)", step.step_id)
        if match:
            max_step_number = max(max_step_number, int(match.group(1)))

    next_number = max_step_number + 1
    built: list[StepDefinition] = []

    for idx, raw_sub_step in enumerate(sub_steps_raw, start=1):
        if not isinstance(raw_sub_step, dict):
            continue

        name = str(raw_sub_step.get("name", "")).strip() or f"{failed_step.name} - sub-step {idx}"
        description = str(raw_sub_step.get("description", "")).strip() or name
        tool_needed = _normalize_tool_needed(raw_sub_step.get("tool_needed"))
        step_id = f"step_{next_number}"
        next_number += 1

        if idx == 1:
            dependencies = list(failed_step.dependencies)
        else:
            dependencies = [built[-1].step_id]

        built.append(
            StepDefinition(
                step_id=step_id,
                name=name,
                description=description,
                tool_needed=tool_needed,
                dependencies=dependencies,
                estimated_complexity=failed_step.estimated_complexity,
            )
        )

    return built


def _apply_abort(state: AgentState, step_index: int, decision: dict[str, Any]) -> None:
    """Handle ABORT by failing the task and logging reflection rationale."""
    step = state["steps"][step_index]
    reason = str(decision.get("reasoning", "")).strip() or "Reflection decided to abort the task."

    state["status"] = "failed"
    state["error_log"].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step.step_id,
            "error_type": "REFLECTION_ABORT",
            "error_message": reason,
        }
    )


def _append_reflection_completed_trace(
    state: AgentState,
    step_index: int,
    action: str,
    decision: dict[str, Any],
    model_used: str,
    tokens_used: int,
) -> None:
    """Append reflection completion trace entry with selected action metadata."""
    step_id = None
    step_name = None
    if 0 <= step_index < len(state.get("steps", [])):
        step = state["steps"][step_index]
        step_id = step.step_id
        step_name = step.name

    details = {
        "action": action,
        "reasoning": str(decision.get("reasoning", "")),
        "modified_step": str(decision.get("modified_step", ""))[:500],
        "sub_step_count": len(decision.get("sub_steps", []) or []),
        "partial_result_preview": str(decision.get("partial_result", ""))[:200],
    }

    state["execution_trace"].append(
        TraceEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="reflection_completed",
            step_id=step_id,
            step_name=step_name,
            details=details,
            tokens_used=max(0, tokens_used),
            model_used=model_used,
            error=None if action != "ABORT" else str(decision.get("reasoning", "Task aborted by reflection.")),
        ).model_dump()
    )


def _default_partial_result(failed_results: list[StepResult], fallback_text: str) -> str:
    """Build a best-effort partial result when SKIP_STEP has no explicit payload."""
    for result in reversed(failed_results):
        if (result.output or "").strip():
            return result.output.strip()[:1000]

    fallback_clean = (fallback_text or "").strip()
    if fallback_clean:
        return f"Partial result unavailable. Preserving step intent: {fallback_clean[:300]}"
    return "Partial result unavailable after repeated failures."


def _normalize_tool_needed(value: Any) -> str:
    """Normalize tool labels into supported executor tool values."""
    normalized = str(value or "").strip().lower()
    allowed = {"web_search", "api_call", "code_exec", "llm_only", "none"}
    if normalized in allowed:
        return normalized
    if normalized in {"llm", "language_model", "model"}:
        return "llm_only"
    return "llm_only"


def _infer_provider(model_name: str) -> str:
    """Infer provider name from model naming convention."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    return "openai"
