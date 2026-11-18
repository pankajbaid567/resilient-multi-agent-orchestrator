"""Task graph: planner -> (pause for plan approval) -> scheduler <-> step workers -> finalizer.

    START -> planner -> scheduler --Send(step_worker) x N--> step_worker -> scheduler
                           |--> budget_stop -> finalizer -> END
                           '--> finalizer -> END

* The planner writes a dependency graph of steps. The run pauses after planning
  (``interrupt_after``): POST /tasks returns the plan, POST /tasks/{id}/execute resumes it.
* The scheduler dispatches every step whose dependencies have finished, using LangGraph
  ``Send`` (one per step, up to MAX_CONCURRENT_STEPS when PARALLEL_MODE is on, else one).
  Each step runs its own subgraph (agent/step_graph.py) and returns a state delta.
* The checkpointer saves state after every super-step (thread_id = task_id), so a crashed
  or restarted run resumes from the last finished wave of steps.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Awaitable, Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.checkpointing import open_checkpointer
from agent.events import emit, trace
from agent.nodes.finalizer import finalizer_node
from agent.nodes.planner import planner_node
from agent.parallel.dag import ExecutionDAG
from agent.state import AgentState, create_initial_state, final_results_by_step
from agent.step_graph import run_step
from config import get_settings
from services.usage import total_tokens, usage_scope

logger = logging.getLogger(__name__)

NODE_PLANNER = "planner"
NODE_SCHEDULER = "scheduler"
NODE_STEP = "step_worker"
NODE_BUDGET = "budget_stop"
NODE_FINALIZER = "finalizer"

APPEND_KEYS = {"step_results", "execution_trace", "error_log", "validation_log", "agent_messages", "llm_calls"}
MERGE_KEYS = {"retry_counts", "reflection_counts", "agent_assignments"}
MAX_CONTEXT_CHARS_PER_DEPENDENCY = 1_500


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _as_delta(fn: Callable[[dict], Awaitable[dict]], state: AgentState, node: str) -> dict[str, Any]:
    """Run a node written in mutate-and-return style on a copy and return only what changed."""
    working = {k: list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v for k, v in state.items()}
    with usage_scope(node=node) as calls:
        result = await fn(working)
    delta: dict[str, Any] = {}
    for key, value in result.items():
        old = state.get(key)
        if key in APPEND_KEYS:
            new_items = list(value or [])[len(old or []):]
            if new_items:
                delta[key] = new_items
        elif key == "llm_tokens_used":
            continue  # counted from usage records below
        elif key in MERGE_KEYS:
            changed = {k: v for k, v in (value or {}).items() if (old or {}).get(k) != v}
            if changed:
                delta[key] = changed
        elif value != old:
            delta[key] = value
    delta["llm_calls"] = [*delta.get("llm_calls", []), *calls]
    delta["llm_tokens_used"] = total_tokens(calls)
    return delta


def budget_exceeded(state: AgentState) -> str | None:
    """Return a reason when the task used its token budget or passed its deadline."""
    settings = get_settings()
    token_budget = _safe_int(settings.TASK_TOKEN_BUDGET)
    if token_budget > 0 and _safe_int(state.get("llm_tokens_used")) >= token_budget:
        return f"token budget reached ({state.get('llm_tokens_used')} >= {token_budget})"
    deadline_at = _deadline_at(state)
    if deadline_at and datetime.now(timezone.utc) >= deadline_at:
        return f"deadline reached ({settings.TASK_DEADLINE_SECONDS}s)"
    return None


def _deadline_at(state: AgentState) -> datetime | None:
    seconds = _safe_int(get_settings().TASK_DEADLINE_SECONDS)
    started_at = state.get("started_at")
    if seconds <= 0 or not started_at:
        return None
    started = datetime.fromisoformat(str(started_at))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started + timedelta(seconds=seconds)


async def planner(state: AgentState) -> dict[str, Any]:
    started = await emit(state["task_id"], trace("task_started", details={"status": "planning"}))
    delta = await _as_delta(planner_node, state, NODE_PLANNER)
    delta["execution_trace"] = [started, *delta.get("execution_trace", [])]
    steps = delta.get("steps") or state.get("steps") or []
    if delta.get("status") == "failed" or not steps:
        delta["status"] = "failed"
        return delta
    dag = ExecutionDAG.from_steps(steps)
    delta.update(status="planned", execution_dag=dag.to_dict(), execution_levels=dag.get_execution_levels())
    delta["execution_trace"].append(await emit(state["task_id"], trace(
        "plan_ready", details={"step_count": len(steps), "levels": delta["execution_levels"]})))
    return delta


async def scheduler(state: AgentState) -> dict[str, Any]:
    finished = final_results_by_step(state)
    return {"status": "executing" if state.get("status") != "failed" else "failed",
            "current_step_index": len(finished)}


def dispatch(state: AgentState) -> list[Send] | str:
    """Send every ready step to a worker, or end the run."""
    if state.get("status") == "failed" or state.get("abort_reason"):
        return NODE_FINALIZER
    if budget_exceeded(state):
        return NODE_BUDGET

    finished = final_results_by_step(state)
    pending = [step for step in state.get("steps") or [] if step.step_id not in finished]
    ready = [step for step in pending if all(dep in finished for dep in step.dependencies)]
    if not ready:
        return NODE_FINALIZER  # all done (or an unsatisfiable dependency, which the planner prevents)

    settings = get_settings()
    limit = max(1, _safe_int(settings.MAX_CONCURRENT_STEPS, 5)) if settings.PARALLEL_MODE else 1
    deadline_at = _deadline_at(state)
    token_budget = _safe_int(settings.TASK_TOKEN_BUDGET)
    tokens_left = token_budget - _safe_int(state.get("llm_tokens_used")) if token_budget > 0 else 0
    reflections_used = sum((state.get("reflection_counts") or {}).values())

    sends = []
    for step in ready[:limit]:
        context = "\n".join(
            f"- {dep} ({finished[dep].status}): {(finished[dep].output or '')[:MAX_CONTEXT_CHARS_PER_DEPENDENCY]}"
            for dep in step.dependencies
        )
        sends.append(Send(NODE_STEP, {
            "task_id": state["task_id"],
            "original_input": state["original_input"],
            "step": step,
            "context": context,
            "deadline_at": deadline_at.isoformat() if deadline_at else None,
            "tokens_left": tokens_left,
            "reflections_used_by_task": reflections_used,
        }))
    return sends


async def step_worker(payload: dict[str, Any]) -> dict[str, Any]:
    return await run_step(payload)


async def budget_stop(state: AgentState) -> dict[str, Any]:
    reason = budget_exceeded(state) or "budget exceeded"
    entry = await emit(state["task_id"], trace("budget_exceeded", details={"reason": reason}))
    return {
        "execution_trace": [entry],
        "error_log": [{"timestamp": entry["timestamp"], "step_id": None, "error_type": "BUDGET_EXCEEDED",
                       "error_message": reason}],
    }


def agent_contributions(state: AgentState) -> dict[str, dict]:
    """Per-agent steps handled and tokens used, from this task's final results only."""
    contributions: dict[str, dict] = {}
    for result in final_results_by_step(state).values():
        if not result.agent_name:
            continue
        entry = contributions.setdefault(result.agent_name, {"steps_handled": 0, "total_tokens": 0,
                                                             "roles_executed": []})
        entry["steps_handled"] += 1
        entry["total_tokens"] += result.tokens_used
        if result.agent_role and result.agent_role not in entry["roles_executed"]:
            entry["roles_executed"].append(result.agent_role)
    return contributions


async def finalizer(state: AgentState) -> dict[str, Any]:
    delta = await _as_delta(finalizer_node, state, NODE_FINALIZER)
    total = _safe_int(state.get("llm_tokens_used")) + delta["llm_tokens_used"]
    final_output = delta.get("final_output") or state.get("final_output") or {}
    summary = final_output.get("summary") or {}
    summary["total_tokens"] = total
    summary["llm_calls"] = len(state.get("llm_calls") or []) + len(delta["llm_calls"])
    if state.get("abort_reason"):
        delta["status"] = "failed"
        summary["abort_reason"] = state["abort_reason"]
    final_output["summary"] = summary
    delta["final_output"] = final_output
    delta["agent_contributions"] = agent_contributions(state)
    delta["current_step_index"] = len(final_results_by_step(state))
    return delta


def build_graph(checkpointer: Any, pause_after_plan: bool = True):
    graph = StateGraph(AgentState)
    graph.add_node(NODE_PLANNER, planner)
    graph.add_node(NODE_SCHEDULER, scheduler)
    graph.add_node(NODE_STEP, step_worker)
    graph.add_node(NODE_BUDGET, budget_stop)
    graph.add_node(NODE_FINALIZER, finalizer)
    graph.add_edge(START, NODE_PLANNER)
    graph.add_edge(NODE_PLANNER, NODE_SCHEDULER)
    graph.add_conditional_edges(NODE_SCHEDULER, dispatch, [NODE_STEP, NODE_BUDGET, NODE_FINALIZER])
    graph.add_edge(NODE_STEP, NODE_SCHEDULER)
    graph.add_edge(NODE_BUDGET, NODE_FINALIZER)
    graph.add_edge(NODE_FINALIZER, END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=[NODE_PLANNER] if pause_after_plan else None)


# ---- Runtime: one compiled graph per process ---------------------------------------------

_graph: Any = None


async def init_graph(stack: AsyncExitStack) -> Any:
    """Compile the graph with the configured checkpointer (called from app startup)."""
    global _graph
    _graph = build_graph(await open_checkpointer(stack))
    return _graph


def get_graph() -> Any:
    """Return the process graph, compiling one with an in-memory checkpointer if needed."""
    global _graph
    if _graph is None:
        _graph = build_graph(InMemorySaver())
    return _graph


def reset_graph() -> None:
    """Forget the compiled graph (tests use this for isolation)."""
    global _graph
    _graph = None


def _config(task_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": task_id}, "recursion_limit": 200}


async def plan_task(task_id: str, user_input: str) -> dict[str, Any]:
    """Run the planner and pause; returns the state with the proposed steps."""
    graph = get_graph()
    await graph.ainvoke(create_initial_state(task_id, user_input), config=_config(task_id))
    return (await graph.aget_state(_config(task_id))).values


async def continue_task(task_id: str) -> dict[str, Any]:
    """Resume a paused or interrupted run from its last checkpoint until it finishes."""
    graph = get_graph()
    snapshot = await graph.aget_state(_config(task_id))
    if not snapshot.values:
        raise ValueError(f"No saved state for task {task_id}")
    if snapshot.next:
        await graph.ainvoke(None, config=_config(task_id))
    return (await graph.aget_state(_config(task_id))).values


async def get_task_state(task_id: str) -> dict[str, Any] | None:
    snapshot = await get_graph().aget_state(_config(task_id))
    return dict(snapshot.values) if snapshot.values else None


async def run_agent(task_id: str, user_input: str) -> dict[str, Any]:
    """Plan and execute a task in one call (no pause for approval)."""
    await plan_task(task_id, user_input)
    return await continue_task(task_id)
