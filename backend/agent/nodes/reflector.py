"""Reflector: chooses a recovery strategy (modify, decompose, skip, abort) for a failing step."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

from config import get_settings
from models import StepDefinition
from services.llm_service import LLMError, call_llm

logger = logging.getLogger(__name__)

REFLECTOR_SYSTEM_PROMPT = (
    "You are a senior debugging engineer. A step in an automated task has failed multiple times. "
    "Analyze the failure and recommend a recovery strategy."
)

VALID_ACTIONS = {"MODIFY_STEP", "SKIP_STEP", "DECOMPOSE", "ABORT"}


REFLECTION_PROMPT = """## Task
{original_input}

## Failed step
Step ID: {step_id}
Name: {name}
Description: {description}

## What went wrong
Attempts so far: {attempts}
Last output (truncated): {last_output}
Last error: {last_error}
Reviewer feedback across attempts:
{feedback}

## Strategies (choose ONE)
1. MODIFY_STEP: rewrite the step description so the next attempt can succeed. Use when the instructions were ambiguous, too broad, or asked for something unavailable.
2. DECOMPOSE: split the step into 2-3 smaller sub-steps that run in order. Use when the step is too big for one attempt.
3. SKIP_STEP: give up on this step and keep the best partial result. Use when the step is not essential.
4. ABORT: stop the whole task. Use ONLY when the task cannot be completed at all.

Respond with JSON:
{{"action": "MODIFY_STEP|DECOMPOSE|SKIP_STEP|ABORT", "reasoning": "...", "modified_step": "new description (MODIFY_STEP only)",
  "sub_steps": [{{"name": "...", "description": "..."}}], "partial_result": "best partial result (SKIP_STEP only)"}}"""


async def decide_recovery(
    original_input: str,
    step: StepDefinition,
    attempts: int,
    last_output: str,
    last_error: str | None,
    feedback: list[str],
) -> dict[str, Any]:
    """Ask the model for a recovery strategy for a step that kept failing validation."""
    prompt = REFLECTION_PROMPT.format(
        original_input=original_input,
        step_id=step.step_id,
        name=step.name,
        description=step.description,
        attempts=attempts,
        last_output=(last_output or "EMPTY")[:600],
        last_error=last_error or "none",
        feedback="\n".join(f"- {item}" for item in feedback[-4:]) or "- none",
    )
    decision, model_used, _tokens = await _get_reflection_decision(prompt)
    action = _normalize_action(str(decision.get("action", "")))
    if action not in VALID_ACTIONS:
        decision["reasoning"] = f"Unrecognised action {decision.get('action')!r}; defaulted to SKIP_STEP."
        action = "SKIP_STEP"
    decision["action"] = action
    decision["sub_steps"] = [sub for sub in decision.get("sub_steps") or [] if sub.get("description")][:3]
    decision["model_used"] = model_used
    return decision


async def _get_reflection_decision(prompt: str) -> tuple[dict[str, Any], str, int]:
    """Call LLM for reflection and return parsed decision + model metadata."""
    settings = get_settings()
    model_name = settings.primary_model
    provider = _infer_provider(model_name)

    try:
        response = await call_llm(
            prompt=prompt,
            system_prompt=REFLECTOR_SYSTEM_PROMPT,
            model=model_name,
            provider=provider,
            temperature=0.2,
            max_tokens=1200,
            json_mode=True,
            timeout=60,
        )
        parsed = _parse_reflection_response(response.text)
        return parsed, response.model_used, response.tokens_used
    except LLMError as exc:
        logger.warning("reflector_llm_error error=%s", exc)
    except Exception as exc:  # pragma: no cover - defensive parser/transport guard
        logger.warning("reflector_unexpected_error error=%s", exc)

    return {
        "action": "SKIP_STEP",
        "reasoning": "Reflection model unavailable; defaulted to SKIP_STEP.",
        "modified_step": "",
        "sub_steps": [],
        "partial_result": "",
    }, "", 0


def _parse_reflection_response(raw_text: str) -> dict[str, Any]:
    """Parse reflection response via JSON, then heuristic extraction if parsing fails."""
    payload = _extract_json_payload(raw_text)
    candidates = [payload, raw_text]

    for candidate in candidates:
        if not candidate.strip():
            continue

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return _normalize_decision_dict(parsed)
        except json.JSONDecodeError:
            pass

        try:
            parsed_literal = ast.literal_eval(candidate)
            if isinstance(parsed_literal, dict):
                return _normalize_decision_dict(parsed_literal)
        except (ValueError, SyntaxError):
            continue

    heuristic_action = _heuristic_action(raw_text)
    if heuristic_action is None:
        heuristic_action = "SKIP_STEP"

    return {
        "action": heuristic_action,
        "reasoning": "Used heuristic action extraction because reflection response was not valid JSON.",
        "modified_step": "",
        "sub_steps": [],
        "partial_result": "",
    }


def _extract_json_payload(text: str) -> str:
    """Extract JSON-like payload from plain or fenced text."""
    stripped = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _normalize_decision_dict(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize parsed reflection JSON into expected decision schema."""
    sub_steps_raw = parsed.get("sub_steps")
    sub_steps: list[dict[str, str]] = []

    if isinstance(sub_steps_raw, list):
        for entry in sub_steps_raw:
            if not isinstance(entry, dict):
                continue
            sub_steps.append(
                {
                    "name": str(entry.get("name", "")).strip(),
                    "description": str(entry.get("description", "")).strip(),
                    "tool_needed": _normalize_tool_needed(entry.get("tool_needed")),
                }
            )

    return {
        "action": _normalize_action(str(parsed.get("action", ""))),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
        "modified_step": str(parsed.get("modified_step", "")).strip(),
        "sub_steps": sub_steps,
        "partial_result": str(parsed.get("partial_result", "")).strip(),
    }


def _heuristic_action(raw_text: str) -> str | None:
    """Heuristically infer action from unstructured model output."""
    text = (raw_text or "").upper()

    patterns = [
        (r"\bMODIFY_STEP\b|\bMODIFY STEP\b|\bMODIFY\b", "MODIFY_STEP"),
        (r"\bSKIP_STEP\b|\bSKIP STEP\b|\bSKIP\b", "SKIP_STEP"),
        (r"\bDECOMPOSE\b|\bBREAK\s+.*\s+SUB[-\s]?STEPS?\b", "DECOMPOSE"),
        (r"\bABORT\b|\bSTOP\s+THE\s+ENTIRE\s+TASK\b", "ABORT"),
    ]

    for pattern, action in patterns:
        if re.search(pattern, text):
            return action
    return None


def _normalize_action(action: str) -> str:
    """Normalize action values into canonical uppercase strategy names."""
    normalized = action.strip().upper().replace(" ", "_")
    if normalized == "MODIFY":
        return "MODIFY_STEP"
    if normalized == "SKIP":
        return "SKIP_STEP"
    return normalized


def _normalize_tool_needed(value: Any) -> str:
    """Normalize tool labels into supported executor tool values."""
    normalized = str(value or "").strip().lower()
    allowed = {"web_search", "api_call", "code_exec", "llm_only", "none"}
    if normalized in allowed:
        return normalized
    if normalized in {"llm", "language_model", "model"}:
        return "llm_only"
    return "llm_only"


def _infer_provider(model_name: str) -> str:
    """Infer provider name from model naming convention."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    return "openai"
