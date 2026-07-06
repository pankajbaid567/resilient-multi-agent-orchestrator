"""Trace models for task execution history and response payloads."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from .task import TraceEntry


class TraceResponse(BaseModel):
    """API response payload containing trace events and aggregate metadata."""

    task_id: str = Field(..., description="Task UUID associated with this trace stream.")
    total_events: int = Field(..., ge=0, description="Total number of events in this response.")
    total_duration_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Aggregate duration in milliseconds across trace events when available.",
    )
    events: List[TraceEntry] = Field(default_factory=list, description="Chronological trace entries.")

    @model_validator(mode="after")
    def normalize_aggregates(self) -> "TraceResponse":
        """Keep aggregate metadata consistent with the provided event list."""
        self.total_events = len(self.events)
        if self.total_duration_ms is None:
            aggregate_duration = sum(entry.duration_ms or 0 for entry in self.events)
            self.total_duration_ms = aggregate_duration if aggregate_duration > 0 else 0
        return self


TraceEvent = TraceEntry

