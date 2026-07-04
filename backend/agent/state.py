"""Agent state definitions shared across LangGraph nodes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, TypedDict

from models import StepDefinition, StepResult


class AgentState(TypedDict):
    """Mutable state that flows through the multi-step execution graph."""

    task_id: str
    original_input: str
    steps: List[StepDefinition]
    current_step_index: int
    step_results: List[StepResult]
    execution_trace: List[dict]
    retry_counts: Dict[str, int]
    reflection_counts: Dict[str, int]
    error_log: List[dict]
    context_memory: List[str]
    llm_tokens_used: int
    status: str
    started_at: str
    completed_at: Optional[str]
    final_output: Optional[dict]
    confidence_score: Optional[str]
    task_metrics: Optional[dict]
    execution_dag: Optional[dict]
    execution_levels: List[List[str]]
    agent_assignments: Dict[str, str]
    agent_contributions: Dict[str, dict]


def create_initial_state(task_id: str, user_input: str) -> AgentState:
    """Factory function that returns a clean initial state with all fields set to defaults."""
    return AgentState(
        task_id=task_id,
        original_input=user_input,
        steps=[],
        current_step_index=0,
        step_results=[],
        execution_trace=[],
        retry_counts={},
        reflection_counts={},
        error_log=[],
        context_memory=[],
        llm_tokens_used=0,
        status="planning",
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=None,
        final_output=None,
        confidence_score=None,
        task_metrics=None,
        execution_dag=None,
        execution_levels=[],
        agent_assignments={},
        agent_contributions={},
    )
