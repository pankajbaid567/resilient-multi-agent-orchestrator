"""Graph state with reducers.

Nodes return *deltas*, never the whole state. Append-only logs use list concatenation,
counters add, and maps merge, so parallel step workers (LangGraph ``Send``) can write to the
same keys in one super-step without clobbering each other. The checkpointer stores this
state after every super-step, which is what makes resume exact.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from models import StepDefinition, StepResult


def merge_dict(left: Dict[str, Any] | None, right: Dict[str, Any] | None) -> Dict[str, Any]:
    """Shallow-merge two maps; keys in ``right`` win."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def merge_steps(left: List[StepDefinition] | None, right: List[StepDefinition] | None) -> List[StepDefinition]:
    """Upsert steps by step_id, keeping first-seen order."""
    by_id: Dict[str, StepDefinition] = {}
    for step in list(left or []) + list(right or []):
        by_id[step.step_id] = step
    return list(by_id.values())


def first_reason(left: Optional[str], right: Optional[str]) -> Optional[str]:
    """Keep the first non-empty reason (two parallel steps may both abort)."""
    return left or right


class AgentState(TypedDict, total=False):
    """State of one task run (one LangGraph thread, thread_id == task_id)."""

    task_id: str
    original_input: str
    steps: Annotated[List[StepDefinition], merge_steps]
    step_results: Annotated[List[StepResult], operator.add]
    execution_trace: Annotated[List[dict], operator.add]
    error_log: Annotated[List[dict], operator.add]
    validation_log: Annotated[List[dict], operator.add]
    agent_messages: Annotated[List[dict], operator.add]
    llm_calls: Annotated[List[dict], operator.add]
    llm_tokens_used: Annotated[int, operator.add]
    retry_counts: Annotated[Dict[str, int], merge_dict]
    reflection_counts: Annotated[Dict[str, int], merge_dict]
    agent_assignments: Annotated[Dict[str, str], merge_dict]
    agent_contributions: Dict[str, dict]
    current_step_index: int
    status: str
    abort_reason: Annotated[Optional[str], first_reason]
    started_at: str
    completed_at: Optional[str]
    final_output: Optional[dict]
    confidence_score: Optional[str]
    task_metrics: Optional[dict]
    execution_dag: Optional[dict]
    execution_levels: List[List[str]]


def create_initial_state(task_id: str, user_input: str) -> AgentState:
    """Return the input for a new run; every reducer key starts empty."""
    return AgentState(
        task_id=task_id,
        original_input=user_input,
        steps=[],
        step_results=[],
        execution_trace=[],
        error_log=[],
        validation_log=[],
        agent_messages=[],
        llm_calls=[],
        llm_tokens_used=0,
        retry_counts={},
        reflection_counts={},
        agent_assignments={},
        agent_contributions={},
        current_step_index=0,
        status="planning",
        abort_reason=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=None,
        final_output=None,
        confidence_score=None,
        task_metrics=None,
        execution_dag=None,
        execution_levels=[],
    )


def final_results_by_step(state: AgentState) -> Dict[str, StepResult]:
    """Map each step to its last recorded result (a step may record several)."""
    latest: Dict[str, StepResult] = {}
    for result in state.get("step_results") or []:
        latest[result.step_id] = result
    return latest
