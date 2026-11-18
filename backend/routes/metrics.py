"""Metrics API routes for per-task, aggregate, and provider health views."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agent.reliability.circuit_breaker import get_circuit_breaker_manager
from agent.state import AgentState
from models import AggregateMetrics, TaskMetrics
from services.metrics_service import get_metrics_service
from agent.graph import get_task_state

router = APIRouter(tags=["metrics"])


@router.get("/providers")
async def get_provider_metrics() -> dict[str, dict[str, Any]]:
    """Return provider-level health metrics merged with live circuit breaker states."""
    service = get_metrics_service()
    aggregate = service.get_aggregate_metrics()
    provider_metrics: dict[str, dict[str, Any]] = {
        provider: dict(payload) if isinstance(payload, dict) else {}
        for provider, payload in aggregate.provider_metrics.items()
    }

    breaker_states = await get_circuit_breaker_manager().get_all_states()
    for provider, state in breaker_states.items():
        normalized = provider.strip().lower()
        current = provider_metrics.setdefault(
            normalized,
            {
                "calls": 0,
                "failures": 0,
                "avg_latency": 0.0,
                "circuit_state": "closed",
            },
        )
        current["circuit_state"] = state.get("state", "closed")
        current["failure_rate"] = round(float(state.get("failure_rate", 0.0)), 4)
        current["calls_in_window"] = int(state.get("calls_in_window", 0))
        current["cooldown_remaining_seconds"] = float(state.get("cooldown_remaining_seconds", 0.0))

    return provider_metrics


@router.get("", response_model=AggregateMetrics)
async def get_aggregate_metrics() -> AggregateMetrics:
    """Return aggregate metrics for all recorded tasks in this backend process."""
    service = get_metrics_service()
    return service.get_aggregate_metrics()


@router.get("/{task_id}", response_model=TaskMetrics)
async def get_task_metrics(task_id: str) -> TaskMetrics:
    """Return task metrics computed from the saved run state (the source of truth).

    The cached snapshot is used only when no saved state exists (for example after the
    in-memory checkpointer was reset); it is taken inside the finalizer and misses the
    finalizer's own model call.
    """
    service = get_metrics_service()
    state = await get_task_state(task_id)
    if state is None:
        cached = service.get_task_metrics(task_id)
        if cached is not None:
            return cached
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    typed_state: AgentState = state  # type: ignore[assignment]
    service.record_task_metrics(task_id, typed_state)
    refreshed = service.get_task_metrics(task_id)
    if refreshed is None:
        raise HTTPException(status_code=500, detail=f"Failed to compute metrics for task: {task_id}")

    return refreshed
