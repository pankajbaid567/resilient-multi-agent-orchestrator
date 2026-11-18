"""Trace entries and live events.

``trace(...)`` builds a validated TraceEntry dict for the state delta; ``publish(...)``
pushes the same event to the task's live stream. Publishing never raises: observability
must not change the outcome of a run.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from models import TraceEntry
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace(event_type: str, step_id: str | None = None, step_name: str | None = None, **fields: Any) -> dict:
    """Return a TraceEntry as a plain dict (validated against the allowed event types)."""
    return TraceEntry(timestamp=now_iso(), event_type=event_type, step_id=step_id, step_name=step_name,
                      **fields).model_dump()


async def publish(task_id: str, event_type: str, **data: Any) -> None:
    """Best-effort live event for WebSocket subscribers."""
    payload = {"event_type": event_type, "timestamp": now_iso(), "task_id": task_id, **data}
    try:
        await get_redis_service().publish_event(task_id, payload)
    except Exception as exc:  # pragma: no cover - observability must not break runs
        logger.warning("event_publish_failed task_id=%s event_type=%s error=%s", task_id, event_type, exc)


async def emit(task_id: str, entry: dict) -> dict:
    """Publish a trace entry as a live event and return it for the state delta."""
    data = {k: v for k, v in entry.items() if k not in {"event_type", "timestamp"} and v is not None}
    await publish(task_id, entry["event_type"], **data)
    return entry
