"""Validator node that decides pass, retry, or reflect for step output quality."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from agent.state import AgentState
from config import get_settings
from models import TraceEntry
from services.llm_service import LLMError, call_llm

logger = logging.getLogger(__name__)

VALIDATOR_SYSTEM_PROMPT = (
    "You are a strict quality assurance judge for AI-generated content. "
    "Your job is to evaluate whether a step's output meets the requirements. "
    "Be concise and fair."
)

VALIDATOR_USER_PROMPT_TEMPLATE = """Task Step: {step_name}
Step Instructions: {step_description}

Output Produced:
{step_output}

Evaluate on these criteria (score each 0-10):
1. RELEVANCE: Does the output address the step's instructions?
2. COMPLETENESS: Is the output thorough (not partial or truncated)?
3. CONSISTENCY: Does it align with the overall task context?
4. PLAUSIBILITY: Is it factually reasonable (not hallucinated)?

Rules:
- If ALL scores >= 6: verdict is 'pass'
- If any score is 3-5: verdict is 'retry' (fixable with another attempt)
- If any score < 3: verdict is 'reflect' (fundamentally wrong, needs rethinking)

Respond with JSON only:
{{
  "verdict": "pass|retry|reflect",
  "reason": "brief explanation",
  "scores": {{"relevance": N, "completeness": N, "consistency": N, "plausibility": N}}
}}"""

HALLUCINATION_MARKERS = [
    "As an AI",
    "I cannot",
    "I don't have access",
    "I'm unable to",
    "I apologize",
]


def rule_based_validate(output: str, step_description: str) -> dict:
    """Evaluate output with deterministic checks when LLM validation is unavailable."""
    if len((output or "").strip()) <= 50:
        return {"verdict": "retry", "reason": "failed rule-based check: output length <= 50"}

    output_lower = output.lower()
    for marker in HALLUCINATION_MARKERS:
        if marker.lower() in output_lower:
            return {
                "verdict": "retry",
                "reason": f"failed rule-based check: hallucination marker found ({marker})",
            }

    keywords = _extract_keywords(step_description)
    keyword_hits = 0
    for keyword in keywords:
        if re.search(rf"\b{re.escape(keyword)}\b", output_lower):
            keyword_hits += 1

    if keyword_hits < 2:
        return {
            "verdict": "retry",
            "reason": "failed rule-based check: fewer than 2 step-description keywords in output",
        }

    return {"verdict": "pass", "reason": "rule-based pass"}


async def validator_node(state: AgentState) -> AgentState:
    """Validate the latest step result. Returns verdict: pass/retry/reflect."""
    state["status"] = "validating"
    latest_result = state["step_results"][-1] if state["step_results"] else None

    if latest_result is None:
        _append_validator_log(
            state=state,
            step_id="step_0",
            verdict="retry",
            reason="No step result available for validation.",
            scores={},
        )
        return state

    step = _find_step_by_id(state=state, step_id=latest_result.step_id)
    step_name = step.name if step is not None else latest_result.step_id
    step_description = step.description if step is not None else ""
    output_text = latest_result.output or ""

    verdict = "retry"
    reason = "Validation fallback applied"
    scores: dict[str, int] = {}

    try:
        model_name = _get_validation_model()
        provider_name = _provider_for_model(model_name)
        validation_prompt = VALIDATOR_USER_PROMPT_TEMPLATE.format(
            step_name=step_name,
            step_description=step_description,
            step_output=output_text,
        )

        llm_response = await call_llm(
            prompt=validation_prompt,
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            model=model_name,
            provider=provider_name,
            temperature=0.1,
            max_tokens=1000,
            json_mode=True,
            timeout=45,
        )
        parsed = _parse_validation_response(llm_response.text)
        verdict = parsed["verdict"]
        reason = parsed["reason"]
        scores = parsed["scores"]
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("validator_llm_unavailable step_id=%s error=%s", latest_result.step_id, exc)
        fallback = rule_based_validate(output=output_text, step_description=step_description)
        verdict = str(fallback.get("verdict", "retry"))
        reason = str(fallback.get("reason", "failed rule-based validation"))
        scores = {}
    except Exception as exc:  # pragma: no cover - defensive safety path
        logger.warning("validator_unexpected_error step_id=%s error=%s", latest_result.step_id, exc)
        fallback = rule_based_validate(output=output_text, step_description=step_description)
        verdict = str(fallback.get("verdict", "retry"))
        reason = str(fallback.get("reason", "failed rule-based validation"))
        scores = {}

    latest_result.validation = verdict

    event_type = "step_completed" if verdict == "pass" else "step_failed"
    state["execution_trace"].append(
        TraceEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            step_id=latest_result.step_id,
            step_name=step_name,
            details={
                "validator_verdict": verdict,
                "validator_reason": reason,
                "scores": scores,
                "output_length": len(output_text),
            },
            error=None if verdict == "pass" else reason,
        ).model_dump()
    )

    _append_validator_log(
        state=state,
        step_id=latest_result.step_id,
        verdict=verdict,
        reason=reason,
        scores=scores,
    )
    return state


def _parse_validation_response(raw_text: str) -> dict[str, Any]:
    """Parse validator JSON output and normalize verdict, reason, and scores."""
    payload = _extract_json_payload(raw_text)
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Validator response must be a JSON object")

    verdict = str(parsed.get("verdict", "")).strip().lower()
    reason = str(parsed.get("reason", "")).strip() or "Validator did not provide a reason"
    scores_raw = parsed.get("scores", {})

    if not isinstance(scores_raw, dict):
        raise ValueError("Validator scores must be a JSON object")

    scores = {
        "relevance": _normalize_score(scores_raw.get("relevance")),
        "completeness": _normalize_score(scores_raw.get("completeness")),
        "consistency": _normalize_score(scores_raw.get("consistency")),
        "plausibility": _normalize_score(scores_raw.get("plausibility")),
    }

    derived_verdict = _derive_verdict_from_scores(scores)
    if verdict not in {"pass", "retry", "reflect"}:
        verdict = derived_verdict
    elif verdict != derived_verdict:
        verdict = derived_verdict

    return {"verdict": verdict, "reason": reason, "scores": scores}


def _extract_json_payload(raw_text: str) -> str:
    """Extract JSON object from plain or fenced response text."""
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _normalize_score(value: Any) -> int:
    """Normalize score values to 0-10 integer bounds."""
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        numeric = 0
    return max(0, min(10, numeric))


def _derive_verdict_from_scores(scores: dict[str, int]) -> str:
    """Apply deterministic verdict rules from normalized score values."""
    values = list(scores.values())
    if values and all(score >= 6 for score in values):
        return "pass"
    if any(score < 3 for score in values):
        return "reflect"
    return "retry"


def _extract_keywords(step_description: str) -> list[str]:
    """Extract relevant lowercase keywords from a step description."""
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "then",
        "step",
        "task",
        "using",
        "your",
        "have",
        "will",
    }

    words = re.findall(r"[A-Za-z0-9]+", step_description.lower())
    keywords: list[str] = []
    for word in words:
        if len(word) < 4:
            continue
        if word in stop_words:
            continue
        if word not in keywords:
            keywords.append(word)
    return keywords[:12]


def _append_validator_log(
    state: AgentState,
    step_id: str,
    verdict: str,
    reason: str,
    scores: dict[str, int],
) -> None:
    """Append validator metadata used by graph routing and observability."""
    state["error_log"].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step_id,
            "validator_verdict": verdict,
            "reason": reason,
            "scores": scores,
        }
    )


def _find_step_by_id(state: AgentState, step_id: str):
    """Return the matching step definition for a given step ID if present."""
    for step in state["steps"]:
        if step.step_id == step_id:
            return step
    return None


def _get_validation_model() -> str:
    """Resolve configured validation model with fallback to a free open-source model."""
    settings = get_settings()
    configured = getattr(settings, "VALIDATION_MODEL", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return "mistralai/Mistral-7B-Instruct-v0.3"


def _provider_for_model(model_name: str) -> str:
    """Infer provider label from model naming while preferring open-source routing."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gpt"):
        return "openai"
    return "open_source"
