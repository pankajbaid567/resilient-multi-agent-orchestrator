"""Task management API routes for planning, retrieval, and resuming execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent.graph import resume_agent
from agent.nodes.planner import planner_node
from agent.reliability.chaos import get_chaos_middleware, set_chaos_mode as set_chaos_runtime_mode
from agent.state import create_initial_state
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])
config_router = APIRouter(tags=["config"])


class TaskCreateRequest(BaseModel):
    """Request body for task creation endpoint."""

    task: str = Field(..., min_length=1, max_length=2000)


class TaskCreateData(BaseModel):
    """Data payload returned when a task is planned successfully."""

    task_id: str
    steps: list[dict[str, Any]]
    status: str


class TaskCreateResponse(BaseModel):
    """Envelope for task-creation responses."""

    success: bool
    data: TaskCreateData | None = None
    error: str | None = None


class TaskStateResponse(BaseModel):
    """Envelope for task state lookup responses."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class ResumeTaskData(BaseModel):
    """Data payload returned when task resume is requested."""

    status: str
    from_step: int


class ResumeTaskResponse(BaseModel):
    """Envelope for resume endpoint responses."""

    success: bool
    data: ResumeTaskData | None = None
    error: str | None = None


class ChaosModeRequest(BaseModel):
    """Request body for runtime chaos-mode toggling."""

    enabled: bool = Field(..., description="Enable or disable runtime chaos injections.")


class ChaosModeResponse(BaseModel):
    """Runtime chaos-mode status and injection counters."""

    chaos_mode: bool
    stats: dict[str, int]


@router.post("", response_model=TaskCreateResponse)
async def create_task(payload: TaskCreateRequest):
    """Create a task, run planning synchronously, checkpoint state, and return planned steps."""
    task_text = payload.task.strip()
    if not task_text:
        return _error_response(status_code=400, message="Task must contain non-whitespace characters")

    task_id = str(uuid4())
    state = create_initial_state(task_id=task_id, user_input=task_text)

    try:
        planned_state = await planner_node(state)
        if str(planned_state.get("status") or "").lower() == "failed":
            return _error_response(status_code=500, message=_planner_error_message(planned_state))

        planned_state["status"] = "planned"
        redis = get_redis_service()
        await redis.save_checkpoint(task_id, planned_state)

        steps = _serialize_steps(planned_state.get("steps") or [])
        return TaskCreateResponse(
            success=True,
            data=TaskCreateData(task_id=task_id, steps=steps, status="planned"),
            error=None,
        )
    except Exception as exc:
        logger.exception("create_task_failed task_id=%s error=%s", task_id, exc)
        return _error_response(status_code=500, message=str(exc))


@router.get("/{task_id}", response_model=TaskStateResponse)
async def get_task(task_id: str):
    """Load task state from Redis checkpoint store."""
    try:
        redis = get_redis_service()
        state = await redis.load_checkpoint(task_id)
        if state is None:
            return _error_response(status_code=404, message=f"Task not found: {task_id}")

        return TaskStateResponse(success=True, data=_normalize_for_response(state), error=None)
    except Exception as exc:
        logger.exception("get_task_failed task_id=%s error=%s", task_id, exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/{task_id}/resume", response_model=ResumeTaskResponse)
async def resume_task(task_id: str):
    """Resume task execution from latest checkpoint in background."""
    try:
        redis = get_redis_service()
        state = await redis.load_checkpoint(task_id)
        if state is None:
            return _error_response(status_code=404, message=f"Task not found: {task_id}")

        from_step = _safe_int(state.get("current_step_index"), default=0)
        asyncio.create_task(_resume_task_background(task_id))
        return ResumeTaskResponse(
            success=True,
            data=ResumeTaskData(status="resumed", from_step=max(0, from_step)),
            error=None,
        )
    except Exception as exc:
        logger.exception("resume_task_failed task_id=%s error=%s", task_id, exc)
        return _error_response(status_code=500, message=str(exc))


@config_router.post("/config/chaos", response_model=ChaosModeResponse)
async def set_chaos_mode(payload: ChaosModeRequest):
    """Enable/disable chaos mode and return updated injection stats."""
    middleware = set_chaos_runtime_mode(bool(payload.enabled))
    return ChaosModeResponse(
        chaos_mode=bool(middleware.enabled),
        stats={str(key): int(value) for key, value in middleware.get_stats().items()},
    )


@config_router.get("/config/chaos", response_model=ChaosModeResponse)
async def get_chaos_mode():
    """Return current runtime chaos mode state and injection stats."""
    middleware = get_chaos_middleware()
    return ChaosModeResponse(
        chaos_mode=bool(middleware.enabled),
        stats={str(key): int(value) for key, value in middleware.get_stats().items()},
    )


async def _resume_task_background(task_id: str) -> None:
    """Background task runner that resumes execution and emits terminal events."""
    redis = get_redis_service()

    try:
        final_state = await resume_agent(task_id)
        await redis.save_checkpoint(task_id, final_state)

        final_status = str(final_state.get("status") or "failed").lower()
        event_type = "task_completed" if final_status == "completed" else "task_failed"
        await redis.publish_event(
            task_id,
            {
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "data": {
                    "status": final_state.get("status"),
                    "current_step_index": final_state.get("current_step_index"),
                },
            },
        )
    except Exception as exc:  # pragma: no cover - background task safety
        logger.exception("resume_task_background_failed task_id=%s error=%s", task_id, exc)
        try:
            await redis.publish_event(
                task_id,
                {
                    "event_type": "task_failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": task_id,
                    "data": {"status": "failed", "error": str(exc)},
                },
            )
        except Exception:
            pass


def _serialize_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Serialize planner step objects into JSON-safe dicts."""
    serialized: list[dict[str, Any]] = []
    for step in steps:
        if hasattr(step, "model_dump"):
            serialized.append(step.model_dump())
        elif isinstance(step, dict):
            serialized.append(dict(step))
    return serialized


def _normalize_for_response(value: Any) -> Any:
    """Recursively normalize model objects for API response serialization."""
    if hasattr(value, "model_dump"):
        return _normalize_for_response(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _normalize_for_response(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_for_response(item) for item in value]
    return value


def _planner_error_message(state: dict[str, Any]) -> str:
    """Extract planner failure message from error_log for API responses."""
    error_log = state.get("error_log")
    if isinstance(error_log, list) and error_log:
        latest = error_log[-1]
        if isinstance(latest, dict) and latest.get("error_message"):
            return str(latest["error_message"])
    return "Planner failed to generate task steps"


def _safe_int(value: Any, default: int = 0) -> int:
    """Best-effort integer conversion helper."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _error_response(status_code: int, message: str) -> JSONResponse:
    """Create standardized error envelope with explicit HTTP status."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": message,
        },
    )
