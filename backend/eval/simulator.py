"""A seeded, role-aware stand-in for an OpenAI-compatible model endpoint.

It exists to test the *orchestration* (retries, fallback, reflection, scheduling) under
controlled failures, not answer quality. Its behaviour is fixed and documented here:

* A worker answer is low quality with probability ``p_bad`` and then carries the marker
  "[low-quality]"; rewriting the step does not change that probability, so reflection gets
  no built-in advantage beyond another attempt.
* The simulated judge is perfect: it fails exactly the marked answers. Real judges are not.
* Profile "outage": the first model in the chain returns HTTP 503 on every call.
* Profile "chaos": the application's own chaos middleware is switched on (see eval/run.py).
* Token counts are len(text)/4 estimates, not provider counts.
"""

from __future__ import annotations

import json
import random
import re
from types import SimpleNamespace

LOW_QUALITY = "[low-quality]"

ROLE_RULES = [
    ("expert task planner", "planner"),
    ("quality assurance judge", "judge"),
    ("supervisor of a team", "supervisor"),
    ("senior debugging engineer", "reflector"),
    ("you are a report synthesizer", "finalizer"),
]


class ProviderHTTPError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status_code = status


def role_of(system_prompt: str) -> str:
    lowered = system_prompt.lower()
    for needle, role in ROLE_RULES:
        if needle in lowered:
            return role
    if "naive-baseline" in lowered:
        return "naive"
    return "worker"


def plan_for(task: str, category: str) -> list[dict]:
    def step(i, name, desc, deps):
        return {"step_id": f"step_{i}", "name": name, "description": desc, "tool_needed": "llm_only",
                "dependencies": deps, "estimated_complexity": "medium"}
    if category == "comparison":
        return [step(1, "Assess option A", f"Assess the first option in: {task}", []),
                step(2, "Assess option B", f"Assess the second option in: {task}", []),
                step(3, "Compare and recommend", f"Compare both assessments and recommend for: {task}",
                     ["step_1", "step_2"])]
    return [step(1, "Gather key points", f"List the key facts and constraints for: {task}", []),
            step(2, "Analyse", f"Analyse the key points for: {task}", ["step_1"]),
            step(3, "Write the result", f"Write the final result for: {task}", ["step_2"])]


class SimulatedProvider:
    def __init__(self, seed: int, profile: str, category: str, primary_model: str, p_bad: float = 0.2):
        self.rng = random.Random(seed)
        self.profile = profile
        self.category = category
        self.primary_model = primary_model
        self.p_bad = p_bad
        self.task_text = ""

    def _respond(self, role: str, prompt: str) -> str:
        if role == "planner":
            match = re.search(r"Task: (.*)", prompt, re.DOTALL)
            self.task_text = (match.group(1).strip() if match else prompt)[:300]
            return json.dumps(plan_for(self.task_text, self.category))
        if role == "supervisor":
            return json.dumps({"agent": "writing" if "Write" in prompt else "analysis",
                               "brief": "Produce exactly what this step asks for, concretely and completely.",
                               "reason": "simulated"})
        if role == "judge":
            output = prompt.split("Output Produced:", 1)[-1]
            if LOW_QUALITY in output:
                return json.dumps({"verdict": "retry", "reason": "Answer is vague and misses required points.",
                                   "scores": {"relevance": 5, "completeness": 4, "consistency": 7, "plausibility": 7}})
            return json.dumps({"verdict": "pass", "reason": "Meets the step requirements.",
                               "scores": {"relevance": 9, "completeness": 8, "consistency": 9, "plausibility": 9}})
        if role == "reflector":
            return json.dumps({"action": "MODIFY_STEP", "reasoning": "Narrow the step to concrete deliverables.",
                               "modified_step": "Produce a concrete, itemised answer for this step only.",
                               "sub_steps": [], "partial_result": ""})
        if role == "finalizer":
            return "Final report combining every completed step into one answer for the task."
        bad = self.rng.random() < self.p_bad
        body = ("A detailed, specific answer that covers the requested points with concrete reasoning, "
                "trade-offs and a clear conclusion for this part of the task.")
        if role == "naive":
            return f"{LOW_QUALITY} {body}" if bad else body
        return f"Final Answer: {LOW_QUALITY + ' ' if bad else ''}{body}"

    async def call_openai(self, prompt, system_prompt, model, temperature, max_tokens, json_mode, timeout):
        if self.profile == "outage" and model == self.primary_model:
            raise ProviderHTTPError(503)
        text = self._respond(role_of(system_prompt), prompt)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=len(system_prompt + prompt) // 4, completion_tokens=len(text) // 4,
                                  total_tokens=(len(system_prompt + prompt) + len(text)) // 4),
            model=model,
        )
