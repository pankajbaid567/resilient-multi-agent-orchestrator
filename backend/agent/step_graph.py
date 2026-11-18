"""Per-step subgraph: delegate -> execute (ReAct worker) -> validate -> retry | reflect -> finish.

One instance runs for each step the scheduler dispatches (via LangGraph ``Send``), so steps
in the same wave run concurrently and each keeps its own retry and reflection loop. The
subgraph's accumulated logs are returned to the parent graph as a delta.
"""

from __future__ import annotations

import logging
import operator
from dataclasses import asdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.events import emit, publish, trace
from agent.multi_agent.agents import AGENT_REGISTRY, ANALYSIS_AGENT
from agent.multi_agent.supervisor import delegate
from agent.multi_agent.worker import run_worker
from agent.nodes.reflector import decide_recovery
from agent.nodes.validator import judge_output
from agent.state import merge_dict
from config import get_settings
from models import StepDefinition, StepResult
from services.usage import total_tokens, usage_scope

logger = logging.getLogger(__name__)

MAX_STEP_REFLECTIONS = 2

# Keys the parent graph accepts back from a step run (all have reducers in AgentState).
DELTA_KEYS = (
    "step_results",
    "execution_trace",
    "error_log",
    "validation_log",
    "agent_messages",
    "llm_calls",
    "llm_tokens_used",
    "retry_counts",
    "reflection_counts",
    "agent_assignments",
)


class StepState(TypedDict, total=False):
    # Inputs (from the scheduler's Send payload)
    task_id: str
    original_input: str
    step: StepDefinition
    context: str
    deadline_at: Optional[str]
    tokens_left: int
    reflections_used_by_task: int
    # Working state
    working_step: StepDefinition
    attempt: int
    reflections: int
    delegation: Optional[dict]
    feedback: List[str]
    notes: List[str]
    last_result: Optional[StepResult]
    verdict: Optional[str]
    verdict_reason: Optional[str]
    abort_reason: Optional[str]
    reflection_action: Optional[str]
    last_truncated: bool
    # Delta for the parent graph
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _out_of_budget(state: StepState, spent: int = 0) -> str | None:
    if state.get("tokens_left") is not None and state["tokens_left"] > 0 and spent >= state["tokens_left"]:
        return "token budget reached during this step"
    deadline = state.get("deadline_at")
    if deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
        return "deadline reached during this step"
    return None


async def execute_node(state: StepState) -> dict[str, Any]:
    step = state["working_step"]
    task_id = state["task_id"]
    delta: dict[str, Any] = {"execution_trace": [], "agent_messages": []}

    with usage_scope(node="execute", step_id=step.step_id) as calls:
        delegation = state.get("delegation")
        if delegation is None:
            if state.get("attempt", 0) == 0:
                delta["execution_trace"].append(await emit(task_id, trace(
                    "step_started", step.step_id, step.name, details={"tool_hint": step.tool_needed})))
            chosen = await delegate(step, state["original_input"], state.get("context", ""))
            delegation = chosen.to_dict()
            delta["delegation"] = delegation
            delta["agent_assignments"] = {step.step_id: AGENT_REGISTRY[chosen.role].name}
            delta["execution_trace"].append(await emit(task_id, trace(
                "supervisor_decision", step.step_id, step.name, agent_name=AGENT_REGISTRY[chosen.role].name,
                details={"role": chosen.role, "reason": chosen.reason, "source": chosen.source})))
            delta["execution_trace"].append(await emit(task_id, trace(
                "agent_assigned", step.step_id, step.name, agent_name=AGENT_REGISTRY[chosen.role].name,
                details={"agent_role": chosen.role, "routing_reason": chosen.reason})))
            delta["agent_messages"].append({"timestamp": _now(), "step_id": step.step_id, "sender": "supervisor",
                                            "recipient": chosen.role, "kind": "brief", "content": chosen.brief})

        agent = AGENT_REGISTRY.get(delegation["role"], ANALYSIS_AGENT)
        notes = list(state.get("notes") or [])
        if state.get("feedback"):
            notes.append(f"Reviewer feedback on the previous attempt (fix this): {state['feedback'][-1]}")
        result = await run_worker(agent, step, state["original_input"], delegation["brief"],
                                  state.get("context", ""), notes, task_id=task_id)

    for call in result.tool_calls:
        delta["execution_trace"].append(await emit(task_id, trace(
            "tool_called" if call.ok else "tool_failed", step.step_id, step.name, agent_name=agent.name,
            duration_ms=call.latency_ms, error=call.error, details={"tool": call.tool, "input": call.input})))
        if not call.ok:
            delta.setdefault("error_log", []).append({"timestamp": _now(), "step_id": step.step_id,
                                                      "error_type": "TOOL_FAILURE", "tool": call.tool,
                                                      "error_message": call.error or "tool failed"})

    spent = total_tokens(calls)
    delta["llm_calls"] = calls
    delta["llm_tokens_used"] = spent
    delta["last_truncated"] = result.truncated
    delta["agent_messages"].append({"timestamp": _now(), "step_id": step.step_id, "sender": agent.role,
                                    "recipient": "supervisor", "kind": "result" if result.ok else "failure",
                                    "content": (result.output or result.error or "")[:500],
                                    "tool_calls": len(result.tool_calls), "iterations": result.iterations})
    delta["last_result"] = StepResult(
        step_id=step.step_id,
        status="success" if result.ok else "failed",
        output=result.output,
        tokens_used=spent,
        latency_ms=result.latency_ms,
        model_used=result.model_used,
        tool_used=",".join(sorted({c.tool for c in result.tool_calls})) or None,
        tool_result={"calls": [asdict(c) for c in result.tool_calls]} if result.tool_calls else None,
        retry_count=state.get("attempt", 0),
        error=result.error,
        agent_name=agent.name,
        agent_role=agent.role,
    )
    if not result.ok:
        delta.setdefault("error_log", []).append({"timestamp": _now(), "step_id": step.step_id,
                                                  "error_type": "EXECUTION_FAILURE",
                                                  "error_message": result.error or "worker failed"})
    return delta


async def validate_node(state: StepState) -> dict[str, Any]:
    step = state["working_step"]
    result = state["last_result"]
    with usage_scope(node="validate", step_id=step.step_id) as calls:
        if result.status != "success":
            verdict, reason, scores, source = "retry", f"No usable output: {result.error or 'empty'}", {}, "deterministic"
        elif state.get("last_truncated"):
            verdict, reason, scores, source = ("retry", "The answer was cut off by the token limit; give a complete, "
                                               "more concise answer.", {}, "deterministic")
        else:
            judgement = await judge_output(step.name, step.description, result.output)
            verdict, reason, scores, source = judgement.verdict, judgement.reason, judgement.scores, judgement.source
    entry = {"timestamp": _now(), "step_id": step.step_id, "attempt": state.get("attempt", 0),
             "validator_verdict": verdict, "reason": reason, "scores": scores, "source": source}
    return {
        "verdict": verdict,
        "verdict_reason": reason,
        "last_result": result.model_copy(update={"validation": verdict}),
        "validation_log": [entry],
        "llm_calls": calls,
        "llm_tokens_used": total_tokens(calls),
    }


def route_after_validate(state: StepState) -> str:
    if state.get("verdict") == "pass":
        return "finish"
    if _out_of_budget(state):
        return "finish"
    max_retries = max(0, int(get_settings().MAX_RETRIES))
    if state.get("verdict") == "retry" and state.get("attempt", 0) < max_retries:
        return "retry"
    return "reflect"


async def retry_node(state: StepState) -> dict[str, Any]:
    step = state["working_step"]
    attempt = state.get("attempt", 0) + 1
    reason = state.get("verdict_reason") or "validation failed"
    entry = await emit(state["task_id"], trace("retry_triggered", step.step_id, step.name,
                                               details={"retry_count": attempt, "reason": reason}))
    return {
        "attempt": attempt,
        "feedback": [*(state.get("feedback") or []), reason],
        "retry_counts": {step.step_id: attempt},
        "execution_trace": [entry],
    }


async def reflect_node(state: StepState) -> dict[str, Any]:
    step = state["working_step"]
    task_id = state["task_id"]
    reflections = state.get("reflections", 0) + 1
    started = await emit(task_id, trace("reflection_started", step.step_id, step.name,
                                        details={"previous_reflections": reflections - 1}))
    delta: dict[str, Any] = {"reflections": reflections, "reflection_counts": {step.step_id: reflections},
                             "execution_trace": [started]}
    last = state.get("last_result")
    task_cap = int(get_settings().MAX_TOTAL_REFLECTIONS)

    with usage_scope(node="reflect", step_id=step.step_id) as calls:
        if not get_settings().ENABLE_REFLECTION:
            decision = {"action": "SKIP_STEP", "reasoning": "Reflection disabled.", "partial_result": "",
                        "sub_steps": [], "modified_step": ""}
        elif reflections > MAX_STEP_REFLECTIONS or state.get("reflections_used_by_task", 0) >= task_cap:
            decision = {"action": "SKIP_STEP", "reasoning": "Reflection limit reached for this step or task.",
                        "partial_result": "", "sub_steps": [], "modified_step": ""}
        else:
            decision = await decide_recovery(state["original_input"], step, state.get("attempt", 0) + 1,
                                             last.output if last else "", last.error if last else None,
                                             list(state.get("feedback") or []))
        action = decision["action"]

        if action == "MODIFY_STEP":
            new_description = decision.get("modified_step") or (
                f"{step.description}\n\nBe explicit and narrow: produce only what the step asks for.")
            delta.update({"working_step": step.model_copy(update={"description": new_description}),
                          "attempt": 0, "feedback": [], "retry_counts": {step.step_id: 0},
                          "notes": [f"The reflector rewrote this step: {decision.get('reasoning', '')[:200]}"]})
        elif action == "DECOMPOSE" and decision.get("sub_steps"):
            delta["last_result"] = await _run_sub_steps(state, step, decision["sub_steps"], delta)
        elif action == "ABORT":
            delta["abort_reason"] = decision.get("reasoning") or "reflector aborted the task"
            delta.setdefault("error_log", []).append({"timestamp": _now(), "step_id": step.step_id,
                                                      "error_type": "REFLECTION_ABORT",
                                                      "error_message": delta["abort_reason"]})
        else:
            action = "SKIP_STEP"
            partial = decision.get("partial_result") or (last.output if last and last.output else "")
            delta["last_result"] = StepResult(
                step_id=step.step_id, status="skipped", output=partial, tokens_used=0, latency_ms=0,
                model_used=decision.get("model_used", ""), retry_count=state.get("attempt", 0),
                validation="reflect", error=f"Skipped after reflection: {decision.get('reasoning', '')[:300]}",
                agent_name=last.agent_name if last else None, agent_role=last.agent_role if last else None)

    delta["execution_trace"].append(await emit(task_id, trace(
        "reflection_completed", step.step_id, step.name,
        details={"action": action, "reasoning": str(decision.get("reasoning", ""))[:500]})))
    delta["llm_calls"] = calls
    delta["llm_tokens_used"] = total_tokens(calls)
    delta["reflection_action"] = action
    return delta


async def _run_sub_steps(state: StepState, step: StepDefinition, sub_steps: list[dict], delta: dict) -> StepResult:
    """Run DECOMPOSE sub-steps in order (one attempt each) and combine them into the step's result."""
    delegation = state.get("delegation") or {"role": ANALYSIS_AGENT.role, "brief": step.description}
    agent = AGENT_REGISTRY.get(delegation["role"], ANALYSIS_AGENT)
    outputs: list[str] = []
    context = state.get("context", "")
    started = perf_counter()
    for index, sub in enumerate(sub_steps, start=1):
        sub_step = step.model_copy(update={"name": f"{step.name} ({index}/{len(sub_steps)}): {sub.get('name', '')}",
                                           "description": sub["description"]})
        result = await run_worker(agent, sub_step, state["original_input"], sub["description"],
                                  context + ("\n" + "\n".join(outputs) if outputs else ""), [],
                                  task_id=state["task_id"])
        usable = result.ok and not result.truncated
        judgement = await judge_output(sub_step.name, sub["description"], result.output) if usable else None
        delta.setdefault("validation_log", []).append({
            "timestamp": _now(), "step_id": step.step_id, "sub_step": index,
            "validator_verdict": judgement.verdict if judgement else "retry",
            "reason": judgement.reason if judgement else (result.error or "no output"),
            "scores": judgement.scores if judgement else {}, "source": judgement.source if judgement else "deterministic"})
        if usable and judgement and judgement.verdict == "pass":
            outputs.append(f"[{sub.get('name') or f'part {index}'}]\n{result.output}")
    ok = len(outputs) == len(sub_steps)
    return StepResult(
        step_id=step.step_id, status="success" if ok else "skipped", output="\n\n".join(outputs),
        latency_ms=int((perf_counter() - started) * 1000), model_used=agent.preferred_model,
        retry_count=state.get("attempt", 0), validation="pass" if ok else "reflect",
        error=None if ok else f"Decomposed into {len(sub_steps)} parts; {len(outputs)} produced output.",
        agent_name=agent.name, agent_role=agent.role)


def route_after_reflect(state: StepState) -> str:
    if state.get("abort_reason"):
        return "finish"
    if state.get("reflection_action") == "MODIFY_STEP" and not _out_of_budget(state):
        return "execute"
    return "finish"


async def finish_node(state: StepState) -> dict[str, Any]:
    step = state["step"]
    result = state.get("last_result")
    if result is None or (state.get("verdict") != "pass" and result.status == "success"
                          and state.get("reflection_action") not in {"DECOMPOSE"}):
        # Validation never passed and no reflection outcome replaced the result.
        reason = _out_of_budget(state) or state.get("abort_reason") or state.get("verdict_reason") or "not validated"
        base = result or StepResult(step_id=step.step_id, status="failed", output="")
        result = base.model_copy(update={"status": "failed", "error": f"Step did not pass validation: {reason}"})
    result = result.model_copy(update={"step_id": step.step_id})
    event_type = "step_completed" if result.status == "success" else "step_failed"
    entry = await emit(state["task_id"], trace(
        event_type, step.step_id, step.name, agent_name=result.agent_name, duration_ms=result.latency_ms,
        tokens_used=result.tokens_used, model_used=result.model_used, error=result.error,
        details={"status": result.status, "retry_count": result.retry_count,
                 "reflections": state.get("reflections", 0), "validator_verdict": result.validation}))
    await publish(state["task_id"], "step_status", step_id=step.step_id, status=result.status)
    delta: dict[str, Any] = {"step_results": [result], "execution_trace": [entry]}
    if state.get("abort_reason"):
        delta["error_log"] = [{"timestamp": _now(), "step_id": step.step_id, "error_type": "ABORT",
                               "error_message": state["abort_reason"]}]
    return delta


def build_step_graph():
    graph = StateGraph(StepState)
    graph.add_node("execute", execute_node)
    graph.add_node("validate", validate_node)
    graph.add_node("retry", retry_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges("validate", route_after_validate,
                                {"finish": "finish", "retry": "retry", "reflect": "reflect"})
    graph.add_edge("retry", "execute")
    graph.add_conditional_edges("reflect", route_after_reflect, {"execute": "execute", "finish": "finish"})
    graph.add_edge("finish", END)
    # Step runs are not checkpointed on their own: resume granularity is one step.
    return graph.compile(checkpointer=False)


STEP_GRAPH = build_step_graph()


async def run_step(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one step to completion and return only the keys the parent graph merges."""
    initial: dict[str, Any] = {
        **payload,
        "working_step": payload["step"],
        "attempt": 0,
        "reflections": 0,
        "feedback": [],
        "notes": [],
        "delegation": None,
        "last_result": None,
        "abort_reason": None,
        "reflection_action": None,
    }
    step = payload["step"]
    try:
        final = await STEP_GRAPH.ainvoke(initial, config={"recursion_limit": 60})
    except Exception as exc:
        # A bug or unexpected error in one step fails that step, not the whole task.
        logger.exception("step_run_failed step_id=%s", step.step_id)
        timestamp = _now()
        return {
            "step_results": [StepResult(step_id=step.step_id, status="failed", output="",
                                        error=f"Internal error: {type(exc).__name__}: {exc}")],
            "error_log": [{"timestamp": timestamp, "step_id": step.step_id, "error_type": "STEP_CRASH",
                           "error_message": f"{type(exc).__name__}: {exc}"}],
            "execution_trace": [trace("step_failed", step.step_id, step.name, error=str(exc)[:500],
                                      details={"internal_error": True})],
        }
    delta = {key: final[key] for key in DELTA_KEYS if final.get(key)}
    if final.get("abort_reason"):
        delta["abort_reason"] = final["abort_reason"]
    return delta
