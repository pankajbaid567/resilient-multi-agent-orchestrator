"""LangGraph orchestration DAG for planner, executor, validator, reflection, and finalization."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import logging
from typing import Any, Awaitable, Callable, cast

from langgraph.graph import END, StateGraph

from agent.parallel import ExecutionDAG, ParallelExecutor
from config import get_settings
from agent.nodes.executor import executor_node
from agent.nodes.finalizer import finalizer_node
from agent.nodes.planner import planner_node
from agent.nodes.validator import validator_node
from agent.state import AgentState, create_initial_state
from models import StepDefinition, StepResult, TraceEntry
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)
settings = get_settings()

CompiledGraph = Any
START_NODE = "__start__"
NODE_PLANNER = "planner_node"
NODE_EXECUTOR = "executor_node"
NODE_VALIDATOR = "validator_node"
NODE_ADVANCE = "advance_step_node"
NODE_RETRY = "prepare_retry_node"
NODE_REFLECTOR = "reflector_node"
NODE_PARALLEL_EXECUTOR = "parallel_executor_node"
NODE_FINALIZER = "finalizer_node"

STEP_STATUS_VALUES = {
    "pending",
    "running",
    "success",
    "failed",
    "retrying",
    "reflecting",
    "skipped",
}
NodeCallable = Callable[[AgentState], Awaitable[AgentState]]

try:
    from agent.nodes.reflector import reflector_node as _reflector_node
except ImportError:
    _reflector_node = None


def route_after_validation(state: AgentState) -> str:
    """Determine next action based on validation verdict."""
    try:
        if state.get("status") == "failed":
            return "finalizer"

        if not state["steps"]:
            return "finalizer"

        if state["current_step_index"] >= len(state["steps"]):
            return "finalizer"

        if not state["step_results"]:
            return "finalizer"

        latest_result = state["step_results"][-1]
        current_step = state["steps"][state["current_step_index"]]

        if latest_result.validation == "pass":
            if state["current_step_index"] >= len(state["steps"]) - 1:
                return "finalizer"
            return "next_step"

        elif latest_result.validation == "retry":
            step_id = current_step.step_id
            retries = state["retry_counts"].get(step_id, 0)
            if retries >= 3:
                return "reflect"
            return "retry"

        elif latest_result.validation == "reflect":
            step_id = current_step.step_id
            reflections = state["reflection_counts"].get(step_id, 0)
            if reflections >= 2:
                return "finalizer"
            return "reflect"

        return "next_step" if state["current_step_index"] < len(state["steps"]) - 1 else "finalizer"
    except Exception as exc:  # pragma: no cover - defensive routing fallback
        logger.exception("route_after_validation_failed: %s", exc)
        _mark_node_exception(state, "route_after_validation", exc)
        return "finalizer"


async def advance_step_node(state: AgentState) -> AgentState:
    """Increment current_step_index, update step status, and publish step-started event."""
    if state["current_step_index"] < len(state["steps"]):
        state["current_step_index"] += 1

    next_step = None
    if state["current_step_index"] < len(state["steps"]):
        next_step = state["steps"][state["current_step_index"]]
        await _update_step_status(
            task_id=state["task_id"],
            step_index=state["current_step_index"],
            status="running",
        )

    await _publish_event(
        task_id=state["task_id"],
        event_type="step_started",
        data={
            "current_step_index": state["current_step_index"],
            "step_id": next_step.step_id if next_step else None,
            "step_name": next_step.name if next_step else None,
        },
    )
    return state


async def prepare_retry_node(state: AgentState) -> AgentState:
    """Increment retry count, remove last failed result, and mark step as retrying."""
    if state["current_step_index"] >= len(state["steps"]):
        return state

    step_id = state["steps"][state["current_step_index"]].step_id
    state["retry_counts"][step_id] = state["retry_counts"].get(step_id, 0) + 1

    if state["step_results"] and state["step_results"][-1].step_id == step_id:
        state["step_results"].pop()

    timestamp = datetime.now(timezone.utc).isoformat()
    retry_count = state["retry_counts"][step_id]
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="retry_triggered",
            step_id=step_id,
            details={"retry_count": retry_count},
        ).model_dump()
    )
    await _publish_event(
        task_id=state["task_id"],
        event_type="retry_triggered",
        data={"step_id": step_id, "retry_count": retry_count},
    )
    await _update_step_status(
        task_id=state["task_id"],
        step_index=state["current_step_index"],
        status="retrying",
    )
    return state


async def placeholder_reflector(state: AgentState) -> AgentState:
    """Temporary: skip step instead of reflecting."""
    if state["current_step_index"] >= len(state["steps"]):
        return state

    state["status"] = "reflecting"
    step = state["steps"][state["current_step_index"]]
    state["reflection_counts"][step.step_id] = state["reflection_counts"].get(step.step_id, 0) + 1

    reason = "Reflector node unavailable; skipping step."
    if state["step_results"] and state["step_results"][-1].step_id == step.step_id:
        latest = state["step_results"][-1]
        latest.status = "skipped"
        latest.validation = "pass"
        latest.error = latest.error or reason
    else:
        state["step_results"].append(
            StepResult(
                step_id=step.step_id,
                status="skipped",
                output="",
                tokens_used=0,
                latency_ms=0,
                model_used="",
                tool_used=None,
                tool_result=None,
                retry_count=state["retry_counts"].get(step.step_id, 0),
                validation="pass",
                error=reason,
            )
        )

    state["current_step_index"] += 1
    timestamp = datetime.now(timezone.utc).isoformat()
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="reflection_completed",
            step_id=step.step_id,
            step_name=step.name,
            details={"placeholder": True, "reason": reason},
        ).model_dump()
    )
    await _publish_event(
        task_id=state["task_id"],
        event_type="reflection_completed",
        data={"step_id": step.step_id, "placeholder": True, "reason": reason},
    )
    return state


async def parallel_executor_node(state: AgentState) -> AgentState:
    """Execute planned steps through DAG levels when PARALLEL_MODE is enabled."""
    state["status"] = "executing"

    if not state.get("steps"):
        return state

    try:
        dag: ExecutionDAG | None = None
        raw_dag = state.get("execution_dag")
        if isinstance(raw_dag, dict) and raw_dag:
            try:
                dag = ExecutionDAG.from_dict(raw_dag)
            except Exception as exc:  # pragma: no cover - defensive hydration fallback
                logger.warning("parallel_executor_dag_restore_failed task_id=%s error=%s", state.get("task_id"), exc)

        if dag is None:
            dag = ExecutionDAG.from_steps(state["steps"])

        is_valid, validation_error = dag.validate()
        if not is_valid:
            raise ValueError(validation_error or "Parallel execution DAG validation failed")

        levels = dag.get_execution_levels()
        state["execution_dag"] = dag.to_dict()
        state["execution_levels"] = levels

        max_concurrent = max(1, _safe_int(getattr(settings, "MAX_CONCURRENT_STEPS", 5), default=5))
        executor = ParallelExecutor(max_concurrent=max_concurrent)
        updated_state = await executor.execute_dag(dag=dag, state=state)

        if updated_state.get("status") != "failed":
            updated_state["status"] = "executing"
            updated_state["current_step_index"] = min(
                len(updated_state.get("steps", [])),
                len(updated_state.get("step_results", [])),
            )

        return updated_state
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        logger.exception("parallel_executor_node_failed task_id=%s error=%s", state.get("task_id"), exc)
        _mark_node_exception(state, NODE_PARALLEL_EXECUTOR, exc)
        return state


def with_checkpoint(node_func: NodeCallable) -> NodeCallable:
    """Decorator that saves checkpoint after node execution."""

    @wraps(node_func)
    async def wrapper(state: AgentState) -> AgentState:
        result = await node_func(state)
        try:
            redis = get_redis_service()
            await redis.save_checkpoint(result["task_id"], result)
            await redis.publish_event(
                result["task_id"],
                {
                    "event_type": "checkpoint_saved",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "node": node_func.__name__,
                    "step_index": result["current_step_index"],
                    "status": result["status"],
                },
            )
        except Exception as exc:  # pragma: no cover - checkpointing is non-fatal
            logger.warning("Checkpoint save failed: %s", exc)
        return result

    return cast(NodeCallable, wrapper)


def with_executor_status(node_func: NodeCallable) -> NodeCallable:
    """Decorator that updates step status before and after executor runs."""

    @wraps(node_func)
    async def wrapper(state: AgentState) -> AgentState:
        step_index = _safe_current_step_index(state)
        if step_index is not None:
            await _update_step_status(task_id=state["task_id"], step_index=step_index, status="running")

        result = await node_func(state)

        if step_index is None:
            return result

        resolved_status = _resolve_executor_step_status(result=result, step_index=step_index)
        await _update_step_status(
            task_id=result["task_id"],
            step_index=step_index,
            status=resolved_status,
        )
        return result

    return cast(NodeCallable, wrapper)


def build_graph(start_node: str = NODE_PLANNER) -> CompiledGraph:
    """Build and compile the LangGraph state machine."""
    graph = StateGraph(AgentState)

    if bool(getattr(settings, "PARALLEL_MODE", False)):
        if start_node not in {
            NODE_PLANNER,
            NODE_PARALLEL_EXECUTOR,
            NODE_FINALIZER,
        }:
            start_node = NODE_PLANNER

        graph.add_node(NODE_PLANNER, _safe_node(NODE_PLANNER, with_checkpoint(planner_node)))
        graph.add_node(
            NODE_PARALLEL_EXECUTOR,
            _safe_node(NODE_PARALLEL_EXECUTOR, with_checkpoint(parallel_executor_node)),
        )
        graph.add_node(NODE_FINALIZER, _safe_node(NODE_FINALIZER, with_checkpoint(finalizer_node)))

        graph.add_edge(START_NODE, start_node)
        graph.add_edge(NODE_PLANNER, NODE_PARALLEL_EXECUTOR)
        graph.add_edge(NODE_PARALLEL_EXECUTOR, NODE_FINALIZER)
        graph.add_edge(NODE_FINALIZER, END)
        return graph.compile()

    if start_node not in {
        NODE_PLANNER,
        NODE_EXECUTOR,
        NODE_VALIDATOR,
        NODE_ADVANCE,
        NODE_RETRY,
        NODE_REFLECTOR,
        NODE_FINALIZER,
    }:
        start_node = NODE_PLANNER

    graph.add_node(NODE_PLANNER, _safe_node(NODE_PLANNER, with_checkpoint(planner_node)))
    graph.add_node(
        NODE_EXECUTOR,
        _safe_node(NODE_EXECUTOR, with_checkpoint(with_executor_status(executor_node))),
    )
    graph.add_node(NODE_VALIDATOR, _safe_node(NODE_VALIDATOR, with_checkpoint(validator_node)))
    graph.add_node(NODE_ADVANCE, _safe_node(NODE_ADVANCE, advance_step_node))
    graph.add_node(NODE_RETRY, _safe_node(NODE_RETRY, prepare_retry_node))
    graph.add_node(NODE_REFLECTOR, _safe_node(NODE_REFLECTOR, with_checkpoint(_reflector_entry)))
    graph.add_node(NODE_FINALIZER, _safe_node(NODE_FINALIZER, with_checkpoint(finalizer_node)))

    graph.add_edge(START_NODE, start_node)
    graph.add_edge(NODE_PLANNER, NODE_EXECUTOR)
    graph.add_edge(NODE_EXECUTOR, NODE_VALIDATOR)
    graph.add_conditional_edges(
        NODE_VALIDATOR,
        route_after_validation,
        {
            "next_step": NODE_ADVANCE,
            "finalizer": NODE_FINALIZER,
            "retry": NODE_RETRY,
            "reflect": NODE_REFLECTOR,
        },
    )
    graph.add_edge(NODE_ADVANCE, NODE_EXECUTOR)
    graph.add_edge(NODE_RETRY, NODE_EXECUTOR)
    graph.add_edge(NODE_REFLECTOR, NODE_EXECUTOR)
    graph.add_edge(NODE_FINALIZER, END)

    return graph.compile()


def _graph_run_config() -> dict[str, int]:
    """Return safe graph runtime config with recursion limit sized for this workflow."""

    explicit_limit = settings.graph_recursion_limit
    if explicit_limit > 0:
        return {"recursion_limit": explicit_limit}

    max_steps = max(_safe_int(getattr(settings, "MAX_STEPS", 15), default=15), 1)
    # Each logical step can span several graph node transitions.
    derived_limit = max_steps * 8
    return {"recursion_limit": max(derived_limit, 50)}


async def run_agent(task_id: str, user_input: str) -> AgentState:
    """Create initial state, build graph, and run full execution."""
    state = create_initial_state(task_id, user_input)
    await _ensure_task_started_event(state)
    graph = build_graph(start_node=NODE_PLANNER)

    try:
        final_state = await graph.ainvoke(state, config=_graph_run_config())
        return final_state
    except Exception as exc:  # pragma: no cover - defensive top-level fallback
        logger.exception("run_agent_failed task_id=%s error=%s", task_id, exc)
        _mark_node_exception(state, "run_agent", exc)
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["status"] = "failed"
        return state


def build_agent_graph() -> CompiledGraph:
    """Backward-compatible alias for build_graph."""
    return build_graph()


async def resume_agent(task_id: str) -> AgentState:
    """Resume a task from its last checkpoint."""
    redis = get_redis_service()
    loaded_state = await redis.load_checkpoint(task_id)
    if not loaded_state:
        raise ValueError(f"No checkpoint found for task {task_id}")

    if not isinstance(loaded_state, dict):
        raise ValueError(f"Invalid checkpoint payload for task {task_id}")

    state = _hydrate_state_from_checkpoint(loaded_state)
    status = str(state.get("status") or "").lower()

    if status in {"completed", "failed"}:
        return state

    await _ensure_task_started_event(state)

    if bool(getattr(settings, "PARALLEL_MODE", False)):
        if status in {"executing", "running", "validating", "reflecting"}:
            start_node = NODE_PARALLEL_EXECUTOR
        else:
            start_node = NODE_PLANNER
    else:
        if status in {"executing", "validating"}:
            start_node = NODE_EXECUTOR
        elif status == "reflecting":
            start_node = NODE_REFLECTOR
        elif status == "planning":
            start_node = NODE_PLANNER
        else:
            start_node = NODE_EXECUTOR

    graph = build_graph(start_node=start_node)
    try:
        final_state = await graph.ainvoke(state, config=_graph_run_config())
        return final_state
    except Exception as exc:  # pragma: no cover - defensive top-level fallback
        logger.exception("resume_agent_failed task_id=%s error=%s", task_id, exc)
        _mark_node_exception(state, "resume_agent", exc)
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["status"] = "failed"
        return state


async def _reflector_entry(state: AgentState) -> AgentState:
    """Run reflector node if available, otherwise use placeholder fallback."""
    if _reflector_node is None:
        return await placeholder_reflector(state)
    return await _reflector_node(state)


def _safe_node(node_name: str, node_fn: NodeCallable):
    """Wrap node execution and prevent unexpected exceptions from breaking the graph."""

    async def _wrapped(state: AgentState) -> AgentState:
        if state.get("status") == "failed" and node_name != "finalizer_node":
            return state

        try:
            return await node_fn(state)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            logger.exception("node_failed node=%s error=%s", node_name, exc)
            _mark_node_exception(state, node_name, exc)
            return state

    return _wrapped


def _mark_node_exception(state: AgentState, node_name: str, error: Exception) -> None:
    """Record node failure details and mark state as failed for finalization routing."""
    timestamp = datetime.now(timezone.utc).isoformat()
    step_id = None
    if 0 <= state.get("current_step_index", 0) < len(state.get("steps", [])):
        step_id = state["steps"][state["current_step_index"]].step_id

    state["status"] = "failed"
    state["error_log"].append(
        {
            "timestamp": timestamp,
            "step_id": step_id,
            "error_type": "NODE_EXCEPTION",
            "node": node_name,
            "error_message": str(error),
        }
    )
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="task_failed",
            step_id=step_id,
            details={"node": node_name},
            error=str(error),
        ).model_dump()
    )


async def _publish_event(task_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Best-effort event publication to Redis for realtime consumers."""
    payload = {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "data": data,
    }

    try:
        redis = get_redis_service()
        await redis.publish_event(task_id=task_id, event=payload)
    except Exception as exc:  # pragma: no cover - event publication should not break execution
        logger.warning("graph_event_publish_failed task_id=%s event_type=%s error=%s", task_id, event_type, exc)


async def _ensure_task_started_event(state: AgentState) -> None:
    """Ensure task_started exists once in execution trace and is broadcast to subscribers."""
    existing_trace = state.get("execution_trace") or []
    for event in existing_trace:
        if isinstance(event, dict) and str(event.get("event_type") or "") == "task_started":
            return

    timestamp = datetime.now(timezone.utc).isoformat()
    state["execution_trace"].append(
        TraceEntry(
            timestamp=timestamp,
            event_type="task_started",
            details={
                "current_step_index": state.get("current_step_index", 0),
                "status": state.get("status", "planning"),
            },
        ).model_dump()
    )

    await _publish_event(
        task_id=state.get("task_id", ""),
        event_type="task_started",
        data={
            "current_step_index": state.get("current_step_index", 0),
            "status": state.get("status", "planning"),
            "timestamp": timestamp,
        },
    )


async def _update_step_status(task_id: str, step_index: int, status: str) -> None:
    """Best-effort update of step status in Redis without breaking graph execution."""
    normalized_status = _normalize_step_status(status)
    try:
        redis = get_redis_service()
        await redis.update_step_status(task_id=task_id, step_index=step_index, status=normalized_status)
    except Exception as exc:  # pragma: no cover - status updates are non-fatal
        logger.warning(
            "graph_step_status_update_failed task_id=%s step_index=%s status=%s error=%s",
            task_id,
            step_index,
            normalized_status,
            exc,
        )


def _normalize_step_status(status: str) -> str:
    """Normalize arbitrary status labels to the allowed Redis step status values."""
    candidate = str(status or "").strip().lower()
    if candidate in STEP_STATUS_VALUES:
        return candidate
    return "running"


def _safe_current_step_index(state: AgentState) -> int | None:
    """Return a valid current step index when in-bounds, otherwise None."""
    raw_index = state.get("current_step_index")
    if not isinstance(raw_index, int):
        return None
    if raw_index < 0:
        return None
    if raw_index >= len(state.get("steps", [])):
        return None
    return raw_index


def _resolve_executor_step_status(result: AgentState, step_index: int) -> str:
    """Resolve persisted status for the current executor step from node result."""
    if step_index < 0 or step_index >= len(result.get("steps", [])):
        return "running"

    current_step = result["steps"][step_index]
    current_step_id = _extract_step_id(current_step)
    latest_result = result["step_results"][-1] if result.get("step_results") else None
    latest_step_id = _extract_step_id(latest_result)

    if latest_result is not None and latest_step_id == current_step_id:
        return _normalize_step_status(_extract_step_result_status(latest_result))

    if str(result.get("status") or "").lower() == "failed":
        return "failed"

    return "running"


def _extract_step_id(item: Any) -> str:
    """Read step_id from StepDefinition/StepResult object or dict payload."""
    if item is None:
        return ""
    if isinstance(item, dict):
        return str(item.get("step_id") or "")
    return str(getattr(item, "step_id", "") or "")


def _extract_step_result_status(item: Any) -> str:
    """Read status from StepResult object or dict payload."""
    if item is None:
        return "running"
    if isinstance(item, dict):
        return str(item.get("status") or "running")
    return str(getattr(item, "status", "running") or "running")


def _hydrate_state_from_checkpoint(raw_state: dict[str, Any]) -> AgentState:
    """Hydrate Redis checkpoint payload into runtime AgentState model objects."""
    hydrated: dict[str, Any] = dict(raw_state)
    hydrated["steps"] = _hydrate_steps(raw_state.get("steps"))
    hydrated["step_results"] = _hydrate_step_results(raw_state.get("step_results"))
    hydrated["execution_trace"] = list(raw_state.get("execution_trace") or [])
    hydrated["retry_counts"] = {
        str(key): _safe_int(value, default=0)
        for key, value in dict(raw_state.get("retry_counts") or {}).items()
    }
    hydrated["reflection_counts"] = {
        str(key): _safe_int(value, default=0)
        for key, value in dict(raw_state.get("reflection_counts") or {}).items()
    }
    hydrated["error_log"] = list(raw_state.get("error_log") or [])
    hydrated["context_memory"] = [str(item) for item in list(raw_state.get("context_memory") or [])]
    hydrated["llm_tokens_used"] = _safe_int(raw_state.get("llm_tokens_used"), default=0)
    hydrated["status"] = str(raw_state.get("status") or "executing")
    hydrated["started_at"] = str(raw_state.get("started_at") or datetime.now(timezone.utc).isoformat())
    hydrated["completed_at"] = raw_state.get("completed_at")
    hydrated["final_output"] = raw_state.get("final_output")
    hydrated["confidence_score"] = raw_state.get("confidence_score")
    hydrated["task_metrics"] = raw_state.get("task_metrics")
    hydrated["execution_dag"] = raw_state.get("execution_dag")
    hydrated["agent_assignments"] = {
        str(key): str(value)
        for key, value in dict(raw_state.get("agent_assignments") or {}).items()
        if str(key)
    }
    hydrated["agent_contributions"] = {
        str(key): dict(value) if isinstance(value, dict) else {}
        for key, value in dict(raw_state.get("agent_contributions") or {}).items()
    }

    raw_levels = raw_state.get("execution_levels")
    normalized_levels: list[list[str]] = []
    if isinstance(raw_levels, list):
        for level in raw_levels:
            if not isinstance(level, list):
                continue
            normalized_levels.append([str(step_id) for step_id in level if str(step_id)])
    hydrated["execution_levels"] = normalized_levels

    raw_index = _safe_int(raw_state.get("current_step_index"), default=0)
    step_count = len(hydrated["steps"])
    hydrated["current_step_index"] = min(max(raw_index, 0), step_count)
    hydrated["task_id"] = str(raw_state.get("task_id") or "")
    hydrated["original_input"] = str(raw_state.get("original_input") or "")

    return cast(AgentState, hydrated)


def _hydrate_steps(raw_steps: Any) -> list[StepDefinition]:
    """Convert checkpoint step payloads into StepDefinition objects."""
    if not isinstance(raw_steps, list):
        return []

    hydrated_steps: list[StepDefinition] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if isinstance(raw_step, StepDefinition):
            hydrated_steps.append(raw_step)
            continue

        if not isinstance(raw_step, dict):
            continue

        try:
            hydrated_steps.append(StepDefinition(**raw_step))
            continue
        except Exception:
            pass

        step_id = str(raw_step.get("step_id") or f"step_{index}").strip() or f"step_{index}"
        if not step_id.startswith("step_"):
            step_id = f"step_{index}"

        tool_needed = str(raw_step.get("tool_needed") or "llm_only").strip().lower()
        if tool_needed not in {"web_search", "api_call", "code_exec", "llm_only", "none"}:
            tool_needed = "llm_only"

        complexity = str(raw_step.get("estimated_complexity") or "medium").strip().lower()
        if complexity not in {"low", "medium", "high"}:
            complexity = "medium"

        dependencies_raw = raw_step.get("dependencies")
        dependencies: list[str] = []
        if isinstance(dependencies_raw, list):
            dependencies = [str(dep) for dep in dependencies_raw if str(dep)]

        try:
            hydrated_steps.append(
                StepDefinition(
                    step_id=step_id,
                    name=str(raw_step.get("name") or f"Step {index}"),
                    description=str(raw_step.get("description") or raw_step.get("name") or f"Step {index}"),
                    tool_needed=tool_needed,
                    dependencies=dependencies,
                    estimated_complexity=complexity,
                )
            )
        except Exception:
            hydrated_steps.append(
                StepDefinition(
                    step_id=f"step_{index}",
                    name=f"Step {index}",
                    description=f"Recovered step {index}",
                    tool_needed="llm_only",
                    dependencies=[],
                    estimated_complexity="medium",
                )
            )

    return hydrated_steps


def _hydrate_step_results(raw_results: Any) -> list[StepResult]:
    """Convert checkpoint step-result payloads into StepResult objects."""
    if not isinstance(raw_results, list):
        return []

    hydrated_results: list[StepResult] = []
    for index, raw_result in enumerate(raw_results, start=1):
        if isinstance(raw_result, StepResult):
            hydrated_results.append(raw_result)
            continue

        if not isinstance(raw_result, dict):
            continue

        try:
            hydrated_results.append(StepResult(**raw_result))
            continue
        except Exception:
            pass

        status = str(raw_result.get("status") or "failed").strip().lower()
        if status not in {"success", "failed", "skipped"}:
            status = "failed"

        step_id = str(raw_result.get("step_id") or f"step_{index}").strip() or f"step_{index}"
        if not step_id.startswith("step_"):
            step_id = f"step_{index}"

        try:
            hydrated_results.append(
                StepResult(
                    step_id=step_id,
                    status=status,
                    output=str(raw_result.get("output") or ""),
                    tokens_used=_safe_int(raw_result.get("tokens_used"), default=0),
                    latency_ms=_safe_int(raw_result.get("latency_ms"), default=0),
                    model_used=str(raw_result.get("model_used") or ""),
                    tool_used=raw_result.get("tool_used"),
                    tool_result=raw_result.get("tool_result"),
                    retry_count=_safe_int(raw_result.get("retry_count"), default=0),
                    validation=raw_result.get("validation"),
                    error=raw_result.get("error"),
                    agent_name=raw_result.get("agent_name"),
                    agent_role=raw_result.get("agent_role"),
                )
            )
        except Exception:
            continue

    return hydrated_results


def _safe_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion with default fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
