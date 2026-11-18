"""Supervisor: decides which specialist handles a step and writes the brief it receives.

Hierarchical delegation: the supervisor sees the whole task and the step, picks one worker
from the registry and hands it a focused brief. When the model's answer is unusable (or
SUPERVISOR_MODE=rules), deterministic rules pick the worker and the step text is the brief.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import re
from typing import Optional

from config import get_settings
from models import StepDefinition
from services.llm_service import LLMError, call_llm

from .agents import AGENT_REGISTRY, ANALYSIS_AGENT

logger = logging.getLogger(__name__)

SUPERVISOR_SYSTEM_PROMPT = (
    "You are the supervisor of a team of specialist agents. For one step of a larger task you choose "
    "the single best specialist and write the brief they will work from. Reply with JSON only."
)

TOOL_HINT_ROLE = {"web_search": "research", "api_call": "research", "code_exec": "code"}

ROLE_KEYWORDS: dict[str, list[str]] = {
    "research": ["search", "find", "look up", "research", "investigate", "discover", "gather", "collect"],
    "code": ["write code", "implement", "program", "function", "script", "calculate", "compute", "debug"],
    "analysis": ["analyze", "analyse", "compare", "evaluate", "assess", "review", "examine", "contrast"],
    "writing": ["write", "summarize", "summarise", "report", "document", "explain", "describe", "draft"],
}


@dataclass(slots=True)
class Delegation:
    role: str
    brief: str
    reason: str
    source: str  # "llm" or "rules"

    def to_dict(self) -> dict:
        return asdict(self)


def rule_based_role(step: StepDefinition) -> tuple[str, str]:
    """Pick a role from the planner's tool hint, then keywords; analysis when unclear."""
    hinted = TOOL_HINT_ROLE.get(str(step.tool_needed or "").lower())
    if hinted:
        return hinted, f"planner tool hint: {step.tool_needed}"

    text = f"{step.name}\n{step.description}".lower()
    scores = {role: 0 for role in ROLE_KEYWORDS}
    for role, keywords in ROLE_KEYWORDS.items():
        for keyword in keywords:
            if " " in keyword:
                scores[role] += 2 if keyword in text else 0
            else:
                scores[role] += len(re.findall(rf"\b{re.escape(keyword)}\b", text))
    best = max(scores.values())
    winners = [role for role, score in scores.items() if score == best]
    if best > 0 and len(winners) == 1:
        return winners[0], f"keywords matched for {winners[0]}"
    return ANALYSIS_AGENT.role, "no clear signal; generalist"


def _roster() -> str:
    lines = []
    for agent in AGENT_REGISTRY.values():
        tools = ", ".join(agent.tools) or "no tools"
        lines.append(f"- {agent.role}: {agent.description} (tools: {tools})")
    return "\n".join(lines)


def _parse(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def delegate(step: StepDefinition, original_input: str, context: str = "") -> Delegation:
    """Choose the worker for ``step`` and the brief it receives."""
    fallback_role, fallback_reason = rule_based_role(step)
    if str(get_settings().SUPERVISOR_MODE).lower() != "llm":
        return Delegation(fallback_role, step.description, fallback_reason, "rules")

    prompt = (
        f"Overall task:\n{original_input}\n\n"
        f"Step {step.step_id}: {step.name}\n{step.description}\n"
        f"Planner's tool hint: {step.tool_needed}\n\n"
        f"Results available from earlier steps:\n{context[:1500] or 'none'}\n\n"
        f"Specialists:\n{_roster()}\n\n"
        'Reply with JSON: {"agent": "<research|code|analysis|writing>", '
        '"brief": "<2-4 sentences: exactly what this specialist must produce for this step>", '
        '"reason": "<one sentence>"}'
    )
    try:
        response = await call_llm(
            prompt=prompt,
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            model=get_settings().primary_model,
            temperature=0.0,
            max_tokens=300,
            json_mode=True,
            timeout=30,
        )
    except LLMError as exc:
        logger.warning("supervisor_llm_failed step_id=%s error=%s", step.step_id, exc)
        return Delegation(fallback_role, step.description, f"{fallback_reason} (supervisor unavailable)", "rules")

    parsed = _parse(response.text) or {}
    role = str(parsed.get("agent") or "").strip().lower()
    brief = str(parsed.get("brief") or "").strip()
    if role not in AGENT_REGISTRY or len(brief) < 10:
        return Delegation(fallback_role, step.description, f"{fallback_reason} (supervisor reply unusable)", "rules")
    return Delegation(role, brief, str(parsed.get("reason") or "").strip()[:300], "llm")
