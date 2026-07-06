"""Metrics and trace summary models for per-task and aggregate observability."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TaskMetrics(BaseModel):
    """Detailed execution and quality metrics for a single task run."""

    task_id: str
    status: str
    total_steps: int = Field(default=0, ge=0)
    successful_steps: int = Field(default=0, ge=0)
    failed_steps: int = Field(default=0, ge=0)
    skipped_steps: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    total_duration_ms: int = Field(default=0, ge=0)
    avg_step_duration_ms: float = Field(default=0.0, ge=0.0)
    max_step_duration_ms: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    reflection_count: int = Field(default=0, ge=0)
    confidence_score: Optional[str] = None
    quality_scores: List[dict] = Field(default_factory=list)
    models_used: Dict[str, int] = Field(default_factory=dict)
    tools_used: Dict[str, int] = Field(default_factory=dict)
    agents_used: Dict[str, int] = Field(default_factory=dict)
    time_saved_parallel_ms: Optional[int] = Field(default=None, ge=0)
    failure_types: Dict[str, int] = Field(default_factory=dict)
    reflection_strategies: Dict[str, int] = Field(default_factory=dict)


class AggregateMetrics(BaseModel):
    """Aggregate metrics computed across all recorded tasks in memory."""

    total_tasks: int = Field(default=0, ge=0)
    completed_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    completion_rate: float = Field(default=0.0, ge=0.0)
    avg_quality_score: float = Field(default=0.0, ge=0.0)
    avg_recovery_rate: float = Field(default=0.0, ge=0.0)
    avg_latency_ms: float = Field(default=0.0, ge=0.0)
    total_tokens_consumed: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    provider_metrics: Dict[str, dict] = Field(default_factory=dict)


class TraceSummary(BaseModel):
    """High-level summary statistics derived from execution trace events."""

    task_id: str
    total_events: int = Field(default=0, ge=0)
    events_by_type: Dict[str, int] = Field(default_factory=dict)
    timeline_start: str = ""
    timeline_end: str = ""
    total_duration_ms: int = Field(default=0, ge=0)
    step_durations: List[dict] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    reflection_count: int = Field(default=0, ge=0)
