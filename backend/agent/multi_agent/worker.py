"""Specialist worker: a ReAct loop (reason, act with a tool, observe) over a text protocol.

The protocol is plain text rather than provider function-calling so that it works with any
chat model, including the small open models this project runs on. The worker may call only
the tools its role allows and the deployment can run; text a model writes after
"Observation:" is discarded, because observations come from tools, never from the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from time import perf_counter

from agent.reliability.circuit_breaker import get_circuit_breaker_manager
from agent.reliability.fallback import call_with_fallback, default_chain
from agent.telemetry import tracer
from agent.tools.registry import AgentTool, tools_for
from config import get_settings
from models import StepDefinition

from .agents import SpecializedAgent

logger = logging.getLogger(__name__)

FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL | re.IGNORECASE)
ACTION_RE = re.compile(r"Action:\s*([A-Za-z_]+)\s*\n+\s*Action Input:\s*(.*)", re.DOTALL | re.IGNORECASE)

PROTOCOL_WITH_TOOLS = """You can use these tools:
{tool_lines}

To use a tool, reply with exactly these two lines and nothing after them:
Action: <tool name>
Action Input: <{input_hint}>

You will then receive an Observation with the tool's output. You may use tools up to {max_calls} times.
When you have enough information, reply with:
Final Answer: <your complete result for this step>

If a tool fails, say in your Final Answer which facts could not be verified. Never invent data."""

PROTOCOL_NO_TOOLS = """Work only from the brief and the context you are given. Reply with:
Final Answer: <your complete result for this step>
If information you need is missing, say so explicitly. Never invent data."""


@dataclass(slots=True)
class ToolCall:
    tool: str
    input: str
    ok: bool
    error: str | None
    latency_ms: int


@dataclass(slots=True)
class WorkerResult:
    ok: bool
    output: str
    model_used: str
    latency_ms: int
    iterations: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False


def build_system_prompt(agent: SpecializedAgent, tools: list[AgentTool], max_calls: int) -> str:
    if not tools:
        return f"{agent.system_prompt}\n\n{PROTOCOL_NO_TOOLS}"
    tool_lines = "\n".join(f"- {tool.name}: {tool.description} Input: {tool.input_hint}." for tool in tools)
    hints = " or ".join(sorted({tool.input_hint for tool in tools}))
    protocol = PROTOCOL_WITH_TOOLS.format(tool_lines=tool_lines, input_hint=hints, max_calls=max_calls)
    return f"{agent.system_prompt}\n\n{protocol}"


def build_user_prompt(
    original_input: str, step: StepDefinition, brief: str, context: str, notes: list[str]
) -> str:
    notes_block = "\n".join(f"- {note}" for note in notes) if notes else "- none"
    return (
        f"Overall task:\n{original_input}\n\n"
        f"Your step ({step.step_id}): {step.name}\n"
        f"Brief from the supervisor:\n{brief}\n\n"
        f"Results from the steps this one depends on:\n{context or '- none'}\n\n"
        f"Notes for this attempt:\n{notes_block}\n"
    )


def parse_reply(text: str) -> tuple[str, str | None, str | None]:
    """Return (kind, a, b): ("final", answer, None), ("action", tool, input) or ("plain", text, None)."""
    reply = text.split("\nObservation:", 1)[0].strip()  # drop observations the model invented
    action = ACTION_RE.search(reply)
    final = FINAL_RE.search(reply)
    if action and (not final or action.start() < final.start()):
        return "action", action.group(1).strip().lower(), action.group(2).strip()
    if final:
        return "final", final.group(1).strip(), None
    return "plain", reply, None


async def run_worker(*args, **kwargs) -> WorkerResult:
    """Run the agent's ReAct loop for one step inside an ``agent <role>`` span."""
    agent: SpecializedAgent = kwargs.get("agent") or args[0]
    with tracer().start_as_current_span(f"agent {agent.role}", attributes={"agent.role": agent.role}) as span:
        result = await _run_worker(*args, **kwargs)
        span.set_attributes({"agent.iterations": result.iterations, "agent.tool_calls": len(result.tool_calls),
                             "agent.ok": result.ok})
        return result


async def _run_worker(
    agent: SpecializedAgent,
    step: StepDefinition,
    original_input: str,
    brief: str,
    context: str,
    notes: list[str],
    task_id: str | None = None,
) -> WorkerResult:
    """Run the agent's ReAct loop for one step and return its final answer."""
    settings = get_settings()
    max_calls = max(0, int(settings.MAX_AGENT_TOOL_CALLS))
    tools = tools_for(agent.tools) if max_calls > 0 else []
    tool_by_name = {tool.name: tool for tool in tools}

    if step.tool_needed in {"web_search", "api_call", "code_exec"} and tools:
        notes = [*notes, f"The planner expected this step to need a tool ({step.tool_needed})."]

    system_prompt = build_system_prompt(agent, tools, max_calls)
    transcript = build_user_prompt(original_input, step, brief, context, notes)
    started = perf_counter()
    tool_calls: list[ToolCall] = []
    model_used = agent.preferred_model or settings.primary_model
    iterations = 0
    forced_final = False

    try:
        while True:
            iterations += 1
            out_of_calls = len(tool_calls) >= max_calls or not tools
            prompt = transcript if not out_of_calls or not tool_calls else (
                f"{transcript}\nYou cannot call more tools. Give your Final Answer now."
            )
            response = await call_with_fallback(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=agent.temperature,
                max_tokens=4096,
                timeout=max(1, int(settings.STEP_TIMEOUT)),
                task_id=task_id,
                step_id=step.step_id,
                fallback_chain=default_chain(agent.preferred_model or None),
                circuit_breaker=get_circuit_breaker_manager(),
            )
            model_used = response.model_used or model_used
            kind, first, second = parse_reply(response.text)

            if kind == "action" and not out_of_calls:
                tool = tool_by_name.get(first or "")
                call_started = perf_counter()
                if tool is None:
                    observation_text = f"Unknown tool '{first}'. Available tools: {', '.join(tool_by_name) or 'none'}."
                    ok, error = False, "unknown tool"
                else:
                    with tracer().start_as_current_span(f"tool {tool.name}", attributes={"tool.name": tool.name}) as span:
                        observation = await tool.run(second or "")
                        span.set_attribute("tool.ok", observation.ok)
                    observation_text, ok, error = observation.text, observation.ok, observation.error
                tool_calls.append(ToolCall(first or "", (second or "")[:300], ok, error,
                                           int((perf_counter() - call_started) * 1000)))
                transcript += (
                    f"\nAction: {first}\nAction Input: {second}\nObservation:\n{observation_text}\n"
                )
                continue

            if kind == "action":
                if not forced_final and tools:
                    # Out of tool calls but the model asked for another: ask once more for a final answer.
                    forced_final = True
                    continue
                answer = response.text.split("Action:", 1)[0].strip()
            else:
                answer = (first or "").strip()
            if not answer:
                return WorkerResult(False, "", model_used, _elapsed(started), iterations, tool_calls,
                                    "empty final answer")
            return WorkerResult(True, answer, model_used, _elapsed(started), iterations, tool_calls,
                                truncated=response.truncated)
    except Exception as exc:
        logger.warning("worker_failed agent=%s step_id=%s error=%s", agent.role, step.step_id, exc)
        return WorkerResult(False, "", model_used, _elapsed(started), iterations, tool_calls, str(exc))


def _elapsed(started: float) -> int:
    return int((perf_counter() - started) * 1000)
