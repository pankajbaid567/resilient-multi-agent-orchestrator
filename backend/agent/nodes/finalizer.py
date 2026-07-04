"""Finalizer node that aggregates step outputs into the task-level result."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from agent.state import AgentState
from config import get_settings
from models import TraceEntry
from services.llm_service import LLMError, call_llm
from services.metrics_service import get_metrics_service

logger = logging.getLogger(__name__)

MODEL_COST_RATES = {
    "gpt-4o": {"input_per_million": 2.50, "output_per_million": 10.00},
    "gpt-4o-mini": {"input_per_million": 0.15, "output_per_million": 0.60},
    "claude-3-5-sonnet": {"input_per_million": 3.00, "output_per_million": 15.00},
}


def compute_confidence(state: AgentState) -> str:
    """
    Scoring logic:
    - Start with 100 points
    - For each failed step: -20 points
    - For each skipped step: -15 points
    - For each step that needed retries: -5 per retry
    - For each reflection used: -10 per reflection

    Score >= 80: "High"
    Score >= 50: "Medium"
    Score < 50: "Low"
    """
    score = 100

    failed_steps = sum(1 for result in state["step_results"] if result.status == "failed")
    skipped_steps = sum(1 for result in state["step_results"] if result.status == "skipped")
    total_retries = sum(state["retry_counts"].values())
    total_reflections = sum(state["reflection_counts"].values())

    score -= failed_steps * 20
    score -= skipped_steps * 15
    score -= total_retries * 5
    score -= total_reflections * 10

    if score >= 80:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


async def finalizer_node(state: AgentState) -> AgentState:
    """Aggregate all step results into final output with confidence score."""
    now = datetime.now(timezone.utc)

    try:
        confidence = compute_confidence(state)
        synthesized_text, synthesis_model, synthesis_tokens = await _synthesize_final_output(state)

        if synthesis_tokens > 0:
            state["llm_tokens_used"] += synthesis_tokens

        total_steps = len(state["steps"])
        successful_steps = sum(1 for result in state["step_results"] if result.status == "success")
        failed_steps = sum(1 for result in state["step_results"] if result.status == "failed")
        skipped_steps = sum(1 for result in state["step_results"] if result.status == "skipped")
        total_retries = sum(state["retry_counts"].values())
        total_reflections = sum(state["reflection_counts"].values())

        models_used = _collect_models_used(state)
        estimated_cost_usd = _estimate_cost_usd(state)
        total_duration_ms = _compute_total_duration_ms(state["started_at"], now)

        summary_dict = {
            "total_steps": total_steps,
            "successful_steps": successful_steps,
            "failed_steps": failed_steps,
            "skipped_steps": skipped_steps,
            "total_retries": total_retries,
            "total_reflections": total_reflections,
            "total_tokens": state["llm_tokens_used"],
            "estimated_cost_usd": estimated_cost_usd,
            "total_duration_ms": total_duration_ms,
            "models_used": models_used,
            "confidence": confidence,
        }

        fail_due_to_quality = confidence == "Low" and total_steps > 0 and failed_steps > (total_steps / 2)
        final_status = "failed" if fail_due_to_quality else "completed"

        state["final_output"] = {"result": synthesized_text, "summary": summary_dict}
        state["confidence_score"] = confidence
        state["status"] = final_status
        state["completed_at"] = now.isoformat()

        state["execution_trace"].append(
            TraceEntry(
                timestamp=now.isoformat(),
                event_type="task_completed",
                details={
                    "final_status": final_status,
                    "confidence": confidence,
                    "successful_steps": successful_steps,
                    "failed_steps": failed_steps,
                    "skipped_steps": skipped_steps,
                    "summary": summary_dict,
                },
                duration_ms=total_duration_ms,
                tokens_used=synthesis_tokens,
                model_used=synthesis_model,
                error=None if final_status == "completed" else "Low confidence with >50% failed steps.",
            ).model_dump()
        )
        _record_task_metrics_snapshot(state)

        logger.info(
            "finalizer_completed task_id=%s status=%s confidence=%s steps=%s",
            state["task_id"],
            final_status,
            confidence,
            total_steps,
        )
        return state

    except Exception as exc:  # pragma: no cover - defensive no-raise behavior
        logger.exception("finalizer_failed task_id=%s error=%s", state.get("task_id"), exc)
        fallback_text = _fallback_concatenated_output(state)
        now_iso = now.isoformat()

        state["final_output"] = {
            "result": fallback_text,
            "summary": {
                "total_steps": len(state["steps"]),
                "successful_steps": sum(1 for result in state["step_results"] if result.status == "success"),
                "failed_steps": sum(1 for result in state["step_results"] if result.status == "failed"),
                "skipped_steps": sum(1 for result in state["step_results"] if result.status == "skipped"),
                "total_retries": sum(state["retry_counts"].values()),
                "total_reflections": sum(state["reflection_counts"].values()),
                "total_tokens": state["llm_tokens_used"],
                "estimated_cost_usd": _estimate_cost_usd(state),
                "total_duration_ms": _compute_total_duration_ms(state["started_at"], now),
                "models_used": _collect_models_used(state),
                "confidence": compute_confidence(state),
            },
        }
        state["confidence_score"] = compute_confidence(state)
        state["status"] = "failed"
        state["completed_at"] = now_iso
        state["error_log"].append(
            {
                "timestamp": now_iso,
                "step_id": "finalizer",
                "error_type": "FINALIZATION_ERROR",
                "error_message": str(exc),
            }
        )
        state["execution_trace"].append(
            TraceEntry(
                timestamp=now_iso,
                event_type="task_completed",
                details={
                    "final_status": "failed",
                    "reason": str(exc),
                },
                duration_ms=_compute_total_duration_ms(state["started_at"], now),
                error=str(exc),
            ).model_dump()
        )
        _record_task_metrics_snapshot(state)
        return state


async def _synthesize_final_output(state: AgentState) -> tuple[str, str, int]:
    """Generate final synthesized report text with LLM, falling back to concatenation."""
    synthesis_prompt = _build_synthesis_prompt(state)
    settings = get_settings()
    model_name = settings.primary_model if settings.primary_model else "meta-llama/Llama-3.1-8B-Instruct"
    provider = _infer_provider(model_name)

    try:
        response = await call_llm(
            prompt=synthesis_prompt,
            system_prompt="You are a report synthesizer.",
            model=model_name,
            provider=provider,
            temperature=0.2,
            max_tokens=4096,
            json_mode=False,
            timeout=90,
        )
        return response.text.strip(), response.model_used, response.tokens_used
    except LLMError as exc:
        logger.warning("finalizer_synthesis_llm_failed error=%s", exc)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("finalizer_synthesis_unexpected_error error=%s", exc)

    return _fallback_concatenated_output(state), "", 0


def _build_synthesis_prompt(state: AgentState) -> str:
    """Build final synthesis prompt from original task and all step results."""
    step_name_by_id = {step.step_id: step.name for step in state["steps"]}
    result_lines: list[str] = []
    non_success_lines: list[str] = []

    for index, result in enumerate(state["step_results"], start=1):
        step_name = step_name_by_id.get(result.step_id, result.step_id)
        truncated_output = (result.output or "")[:500]
        result_lines.append(f"Step {index} ({step_name}): {truncated_output}")

        if result.status != "success":
            reason = result.error or "No reason provided"
            non_success_lines.append(f"- {result.step_id} ({result.status}): {reason}")

    if not result_lines:
        result_lines.append("No step results available.")

    if not non_success_lines:
        non_success_lines.append("None")

    joined_results = "\n".join(result_lines)
    joined_non_success = "\n".join(non_success_lines)

    return (
        "You are a report synthesizer. Combine these step results into a clear, well-structured final output.\n\n"
        f"Original Task: {state['original_input']}\n\n"
        "Step Results:\n"
        f"{joined_results}\n\n"
        "Skipped/Failed Steps: "
        f"{joined_non_success}\n\n"
        "Create a coherent, comprehensive response that addresses the original task.\n"
        "Format with clear sections and key takeaways."
    )


def _fallback_concatenated_output(state: AgentState) -> str:
    """Fallback final output builder when synthesis LLM call fails."""
    step_name_by_id = {step.step_id: step.name for step in state["steps"]}
    parts: list[str] = ["Final Output (Fallback Aggregation)"]

    for index, result in enumerate(state["step_results"], start=1):
        step_name = step_name_by_id.get(result.step_id, result.step_id)
        body = result.output.strip() if result.output.strip() else "[No output provided]"
        parts.append(f"\n## Step {index}: {step_name}\nStatus: {result.status}\n{body}")

    if len(parts) == 1:
        parts.append("\nNo step outputs were available to aggregate.")

    return "\n".join(parts)


def _estimate_cost_usd(state: AgentState) -> float:
    """Estimate USD cost from step token usage and model-specific rates."""
    total_cost = 0.0
    for result in state["step_results"]:
        model_key = _normalize_model_key(result.model_used)
        rates = MODEL_COST_RATES.get(model_key)
        if rates is None or result.tokens_used <= 0:
            continue

        # StepResult tracks total tokens only; estimate input/output split evenly.
        input_tokens = result.tokens_used // 2
        output_tokens = result.tokens_used - input_tokens
        total_cost += (input_tokens / 1_000_000) * rates["input_per_million"]
        total_cost += (output_tokens / 1_000_000) * rates["output_per_million"]

    return round(total_cost, 8)


def _normalize_model_key(model_name: str) -> str:
    """Normalize model names to known pricing keys."""
    normalized = (model_name or "").strip().lower()
    if normalized.startswith("gpt-4o-mini"):
        return "gpt-4o-mini"
    if normalized.startswith("gpt-4o"):
        return "gpt-4o"
    if "claude" in normalized and "sonnet" in normalized:
        return "claude-3-5-sonnet"
    return ""


def _collect_models_used(state: AgentState) -> list[str]:
    """Collect unique model names observed in step results preserving order."""
    models: list[str] = []
    for result in state["step_results"]:
        model_name = (result.model_used or "").strip()
        if model_name and model_name not in models:
            models.append(model_name)
    return models


def _compute_total_duration_ms(started_at: str, completed_at: datetime) -> int:
    """Compute task duration in milliseconds from started_at to completion time."""
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        started = completed_at

    duration = completed_at - started
    return max(0, int(duration.total_seconds() * 1000))


def _infer_provider(model_name: str) -> str:
    """Infer provider from model naming convention."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gpt"):
        return "openai"
    return "open_source"


def _record_task_metrics_snapshot(state: AgentState) -> None:
    """Best-effort metrics recording that must not break finalization flow."""
    try:
        service = get_metrics_service()
        task_id = str(state.get("task_id") or "")
        if not task_id:
            return

        service.record_task_metrics(task_id=task_id, state=state)
        task_metrics = service.get_task_metrics(task_id)
        state["task_metrics"] = task_metrics.model_dump() if task_metrics is not None else None
    except Exception as exc:  # pragma: no cover - metrics recording is non-fatal
        logger.warning("finalizer_metrics_record_failed task_id=%s error=%s", state.get("task_id"), exc)
