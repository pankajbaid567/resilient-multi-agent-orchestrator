"""Trace retrieval API route for execution observability."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.state import AgentState
from models import TraceEntry
from models.metrics import TraceSummary
from services.redis_service import get_redis_service
from services.metrics_service import get_metrics_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["traces"])


class TraceData(BaseModel):
    """Data payload returned from trace lookup endpoint."""

    task_id: str
    trace: list[dict[str, Any]]
    total_events: int


class TraceResponse(BaseModel):
    """Envelope for trace route responses."""

    success: bool
    data: TraceData | None = None
    error: str | None = None


@router.get("/{task_id}", response_model=TraceResponse)
async def get_trace(task_id: str):
    """Load execution trace timeline for a task from Redis checkpoint state."""
    try:
        redis = get_redis_service()
        state = await redis.load_checkpoint(task_id)
        if state is None:
            return _error_response(status_code=404, message=f"Task not found: {task_id}")

        trace_raw = state.get("execution_trace") if isinstance(state, dict) else []
        trace = _normalize_trace(trace_raw)
        return TraceResponse(
            success=True,
            data=TraceData(task_id=task_id, trace=trace, total_events=len(trace)),
            error=None,
        )
    except Exception as exc:
        logger.exception("get_trace_failed task_id=%s error=%s", task_id, exc)
        return _error_response(status_code=500, message=str(exc))


@router.get("/{task_id}/summary", response_model=TraceSummary)
async def get_trace_summary(task_id: str) -> TraceSummary:
    """Return aggregate trace summary statistics for one task."""
    state = await _load_task_state(task_id)
    service = get_metrics_service()
    return service.get_trace_summary(state)


@router.get("/{task_id}/step/{step_id}", response_model=list[TraceEntry])
async def get_step_trace(task_id: str, step_id: str) -> list[TraceEntry]:
    """Return all trace entries associated with a specific step ID."""
    state = await _load_task_state(task_id)
    trace_raw = state.get("execution_trace") if isinstance(state, dict) else []
    trace = _normalize_trace(trace_raw)

    filtered: list[TraceEntry] = []
    for event in trace:
        if str(event.get("step_id") or "") != step_id:
            continue
        filtered.append(TraceEntry.model_validate(event))

    return filtered


def _normalize_trace(value: Any) -> list[dict[str, Any]]:
    """Normalize trace entries into JSON-safe dictionaries."""
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in value:
        if hasattr(item, "model_dump"):
            normalized.append(item.model_dump())
        elif isinstance(item, dict):
            normalized.append(dict(item))
    return normalized


async def _load_task_state(task_id: str) -> AgentState:
    """Load task state from Redis checkpoint or raise 404 when missing."""
    redis = get_redis_service()
    state = await redis.load_checkpoint(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if not isinstance(state, dict):
        raise HTTPException(status_code=500, detail=f"Invalid checkpoint format for task: {task_id}")
    return state  # type: ignore[return-value]


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
