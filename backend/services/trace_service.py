"""Execution trace service for collecting and persisting timeline events."""

from __future__ import annotations

from models import TraceEvent


async def append_trace_event(event: TraceEvent) -> None:
    """Persist a trace event to durable storage for later retrieval."""
    # TODO: Save event in Redis stream/database and broadcast to websocket subscribers.
    _ = event
