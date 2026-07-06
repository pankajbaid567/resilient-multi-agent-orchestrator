"""Task, step, error, and execution event models for the agent system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_STEP_ID_PATTERN = re.compile(r"^step_\d+$")


def _validate_iso8601(value: str, field_name: str) -> str:
    """Validate an ISO 8601 datetime string and return the original value."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 timestamp") from exc
    return value


def _validate_step_id(value: str, field_name: str = "step_id") -> str:
    """Validate step identifiers like step_1."""
    if not _STEP_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must match pattern step_1")
    return value


class TaskStatus(str, Enum):
    """Supported task lifecycle values across API and internal graph states."""

    pending = "pending"
    planning = "planning"
    planned = "planned"
    executing = "executing"
    running = "running"
    validating = "validating"
    reflecting = "reflecting"
    completed = "completed"
    failed = "failed"


class ToolNeeded(str, Enum):
    """Backward-compatible tool selector enum used by planner stubs."""

    web_search = "web_search"
    api_call = "api_call"
    code_exec = "code_exec"
    llm_only = "llm_only"
    llm = "llm_only"
    none = "none"


class Complexity(str, Enum):
    """Backward-compatible complexity enum used by planner stubs."""

    low = "low"
    medium = "medium"
    high = "high"


class StepDefinition(BaseModel):
    """A single planned step that can be scheduled by the executor."""

    step_id: str = Field(..., description="Stable step identifier, for example step_1.")
    name: str = Field(..., description="Human-readable step title.")
    description: str = Field(..., description="Detailed instruction for the executor.")
    tool_needed: Literal["web_search", "api_call", "code_exec", "llm_only", "none"] = Field(
        ..., description="Primary tool class needed to execute this step."
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="Step IDs that must complete before this step can run.",
    )
    estimated_complexity: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Estimated execution complexity for scheduling and monitoring.",
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: str) -> str:
        """Validate the canonical step identifier format."""
        return _validate_step_id(value)

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: List[str]) -> List[str]:
        """Validate dependency identifiers to prevent invalid DAG references."""
        for dependency in value:
            _validate_step_id(dependency, field_name="dependencies")
        return value


class StepResult(BaseModel):
    """Execution outcome for one completed, failed, or skipped step."""

    step_id: str = Field(..., description="ID of the step this result belongs to.")
    status: Literal["success", "failed", "skipped"] = Field(
        ..., description="Final status of this step attempt."
    )
    output: str = Field(..., description="The actual step output text.")
    tokens_used: int = Field(default=0, ge=0, description="Token usage attributed to this step.")
    latency_ms: int = Field(default=0, ge=0, description="End-to-end latency for this step in milliseconds.")
    model_used: str = Field(default="", description="LLM model name used for this step, if any.")
    tool_used: Optional[str] = Field(default=None, description="Tool used to execute this step, if any.")
    tool_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured raw tool output payload, when available.",
    )
    retry_count: int = Field(default=0, ge=0, description="Number of retries consumed for this step.")
    validation: Optional[str] = Field(default=None, description="Validation decision: pass, retry, or reflect.")
    error: Optional[str] = Field(default=None, description="Error summary when status is failed.")
    agent_name: Optional[str] = Field(default=None, description="Name of the specialized agent assigned to this step.")
    agent_role: Optional[str] = Field(
        default=None,
        description="Role of the specialized agent (research/code/analysis/writing).",
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: str) -> str:
        """Validate the step ID format for result records."""
        return _validate_step_id(value)


class ErrorEntry(BaseModel):
    """Structured error event captured during task execution."""

    timestamp: str = Field(..., description="ISO 8601 timestamp for when the error occurred.")
    step_id: str = Field(..., description="Step identifier associated with this error.")
    error_type: Literal[
        "EMPTY_OUTPUT",
        "PARSE_ERROR",
        "HALLUCINATION",
        "TIMEOUT",
        "CLIENT_ERROR",
        "SERVER_ERROR",
        "RATE_LIMITED",
        "TOKEN_OVERFLOW",
        "QUALITY_FAIL",
        "CONNECTION_ERROR",
    ] = Field(..., description="Categorized error class used for reliability logic.")
    error_message: str = Field(..., description="Human-readable error message.")
    raw_response: Optional[str] = Field(default=None, description="Optional raw provider/tool output.")
    attempt_number: int = Field(default=1, ge=1, description="1-based attempt counter for this error.")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """Validate error timestamps as ISO 8601 strings."""
        return _validate_iso8601(value, "timestamp")

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: str) -> str:
        """Validate step ID format for error records."""
        return _validate_step_id(value)


class TraceEntry(BaseModel):
    """Timeline event used for observability and frontend streaming."""

    timestamp: str = Field(..., description="ISO 8601 timestamp when the event happened.")
    event_type: Literal[
        "task_started",
        "planning_complete",
        "step_started",
        "step_completed",
        "step_failed",
        "retry_triggered",
        "fallback_triggered",
        "reflection_started",
        "reflection_completed",
        "tool_called",
        "parallel_level_started",
        "parallel_step_completed",
        "parallel_level_completed",
        "agent_assigned",
        "agent_handoff",
        "checkpoint_saved",
        "task_completed",
        "task_failed",
    ] = Field(..., description="Canonical trace event type.")
    step_id: Optional[str] = Field(default=None, description="Associated step ID, if event is step-scoped.")
    step_name: Optional[str] = Field(default=None, description="Associated step name, if known.")
    details: Dict[str, Any] = Field(default_factory=dict, description="Event-specific payload details.")
    parent_event_id: Optional[str] = Field(
        default=None,
        description="Links retry/fallback events to the original step event.",
    )
    prompt_preview: Optional[str] = Field(
        default=None,
        description="First 200 characters of the prompt sent to a model.",
    )
    response_preview: Optional[str] = Field(
        default=None,
        description="First 200 characters of the response received from a model.",
    )
    tokens_in: Optional[int] = Field(
        default=None,
        ge=0,
        description="Input/prompt token count for this event.",
    )
    tokens_out: Optional[int] = Field(
        default=None,
        ge=0,
        description="Output/completion token count for this event.",
    )
    provider: Optional[str] = Field(
        default=None,
        description="Model provider used in this event, for example openai or anthropic.",
    )
    circuit_state: Optional[str] = Field(
        default=None,
        description="Circuit breaker state at call time: closed, open, or half_open.",
    )
    agent_name: Optional[str] = Field(
        default=None,
        description="Specialized agent assigned to this event, when multi-agent mode is enabled.",
    )
    level: Optional[int] = Field(
        default=None,
        ge=0,
        description="Parallel execution level for this event.",
    )
    concurrent_with: Optional[List[str]] = Field(
        default=None,
        description="Other step_ids that were executing concurrently with this event.",
    )
    duration_ms: Optional[int] = Field(default=None, ge=0, description="Event duration in milliseconds, if measured.")
    tokens_used: Optional[int] = Field(default=None, ge=0, description="Tokens consumed by this event, if applicable.")
    model_used: Optional[str] = Field(default=None, description="Model identifier used in this event, if any.")
    error: Optional[str] = Field(default=None, description="Optional error payload attached to this event.")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """Validate trace timestamps as ISO 8601 strings."""
        return _validate_iso8601(value, "timestamp")

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: Optional[str]) -> Optional[str]:
        """Validate optional step IDs when present."""
        if value is None:
            return None
        return _validate_step_id(value)


class TaskRequest(BaseModel):
    """Task submission payload accepted by task creation APIs."""

    task: str = Field(..., min_length=1, max_length=2000, description="Task description")


class TaskResponse(BaseModel):
    """Task creation/lookup response payload returned to clients."""

    success: bool = Field(default=True, description="Whether request handling succeeded.")
    task_id: str = Field(..., description="Task UUID.")
    steps: List[StepDefinition] = Field(..., description="Planned step list for the task.")
    status: str = Field(..., description="Current task status.")


class ExecutionEvent(BaseModel):
    """WebSocket event sent to frontend."""

    event_type: str = Field(..., min_length=1, description="Event type label.")
    task_id: str = Field(..., description="Task UUID associated with this event.")
    timestamp: str = Field(..., description="ISO 8601 timestamp for the event.")
    data: Dict[str, Any] = Field(default_factory=dict, description="Event payload data.")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """Validate event timestamps as ISO 8601 strings."""
        return _validate_iso8601(value, "timestamp")


class TaskCreateRequest(BaseModel):
    """Backward-compatible task create payload with input field name."""

    input: str = Field(..., min_length=1, max_length=2000, description="Task description")


class TaskCreateResponse(TaskResponse):
    """Backward-compatible alias for the task creation response schema."""


class ExecuteTaskRequest(BaseModel):
    """Execution control payload used by execute routes."""

    resume_from_checkpoint: bool = Field(default=False, description="Resume task from latest checkpoint when true.")
