"""A scripted stand-in for an OpenAI-compatible chat endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import SimpleNamespace
from typing import Callable

DEFAULT_PLAN = [
    {"step_id": "step_1", "name": "Collect facts", "description": "Collect key facts about solar panels efficiency",
     "tool_needed": "llm_only", "dependencies": [], "estimated_complexity": "low"},
    {"step_id": "step_2", "name": "Write summary", "description": "Write summary of solar panels efficiency facts",
     "tool_needed": "llm_only", "dependencies": ["step_1"], "estimated_complexity": "low"},
]

PASS_VERDICT = {"verdict": "pass", "reason": "ok",
                "scores": {"relevance": 9, "completeness": 9, "consistency": 9, "plausibility": 9}}
RETRY_VERDICT = {"verdict": "retry", "reason": "too vague",
                 "scores": {"relevance": 5, "completeness": 5, "consistency": 8, "plausibility": 8}}


def classify(system_prompt: str, prompt: str) -> str:
    """Name the role a request plays, from the fixed prompts the app uses."""
    text = system_prompt.lower()
    if "expert task planner" in text:
        return "planner"
    if "quality assurance judge" in text:
        return "validator"
    if "supervisor of a team" in text:
        return "supervisor"
    if "senior debugging engineer" in text:
        return "reflector"
    if "classify task steps" in text:
        return "router"
    if "handoff" in text:
        return "handoff"
    if "generate python code" in text:
        return "codegen"
    if "you are a report synthesizer" in text:
        return "finalizer"
    return "executor"


@dataclass
class FakeLLM:
    """Records every call and answers by role. Override a role with a callable or list."""

    calls: list[dict] = field(default_factory=list)
    scripts: dict[str, list] = field(default_factory=dict)
    plan: list[dict] = field(default_factory=lambda: list(DEFAULT_PLAN))
    prompt_tokens: int = 100
    completion_tokens: int = 50

    def script(self, role: str, *responses) -> None:
        """Queue responses for a role; each may be a string, a dict (JSON) or an Exception."""
        self.scripts.setdefault(role, []).extend(responses)

    def calls_for(self, role: str) -> list[dict]:
        return [call for call in self.calls if call["role"] == role]

    async def call_openai(self, prompt, system_prompt, model, temperature, max_tokens, json_mode, timeout):
        role = classify(system_prompt, prompt)
        self.calls.append({"role": role, "model": model, "prompt": prompt, "system": system_prompt})
        queued = self.scripts.get(role)
        if queued:
            item = queued.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, Callable) and not isinstance(item, (str, dict, list)):
                item = item(prompt)
            text = item if isinstance(item, str) else json.dumps(item)
        else:
            text = self._default(role)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens,
                                  total_tokens=self.prompt_tokens + self.completion_tokens),
            model=model,
        )

    def _default(self, role: str) -> str:
        if role == "planner":
            return json.dumps(self.plan)
        if role == "validator":
            return json.dumps(PASS_VERDICT)
        if role == "reflector":
            return json.dumps({"action": "SKIP_STEP", "reasoning": "cannot be fixed", "modified_step": "",
                               "sub_steps": [], "partial_result": "partial"})
        if role == "supervisor":
            return json.dumps({"agent": "analysis", "brief": "Produce the facts this step asks for, clearly.",
                               "reason": "analysis fits"})
        if role == "codegen":
            return "print(42)"
        if role == "finalizer":
            return "Final report: solar panels efficiency summary with collected facts."
        return ("Solar panels efficiency facts: modern panels convert about 20 percent of sunlight. "
                "This summary covers the collected facts in detail.")


def provider_error(status: int, name: str = "APIStatusError") -> Exception:
    """Build an exception shaped like an SDK HTTP error with a status code."""
    error_type = type(name, (Exception,), {})
    error = error_type(f"HTTP {status}")
    error.status_code = status
    return error
