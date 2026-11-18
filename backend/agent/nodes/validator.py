"""Validator node that decides pass, retry, or reflect for step output quality."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from config import get_settings
from agent.reliability.fallback import complete
from services.llm_service import LLMError

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

MAX_JUDGED_OUTPUT_CHARS = 6_000
MIN_OUTPUT_CHARS = 40

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


@dataclass(slots=True)
class Judgement:
    verdict: str  # pass | retry | reflect
    reason: str
    scores: dict[str, int]
    source: str  # "deterministic" | "llm" | "rules"


async def judge_output(step_name: str, step_description: str, output: str) -> Judgement:
    """Decide pass / retry / reflect for one step output.

    Cheap deterministic checks run first; the LLM judge only sees outputs that pass them.
    If the judge is unavailable or its reply cannot be parsed, keyword rules decide.
    """
    text = (output or "").strip()
    if not text:
        return Judgement("retry", "Step produced no output.", {}, "deterministic")
    if len(text) < MIN_OUTPUT_CHARS:
        return Judgement("retry", f"Output is too short ({len(text)} characters) to complete the step.", {},
                         "deterministic")

    try:
        response = await complete(
            preferred_model=_get_validation_model(),
            prompt=VALIDATOR_USER_PROMPT_TEMPLATE.format(
                step_name=step_name,
                step_description=step_description,
                step_output=text[:MAX_JUDGED_OUTPUT_CHARS],
            ),
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=400,
            json_mode=True,
            timeout=45,
        )
        parsed = _parse_validation_response(response.text)
        return Judgement(parsed["verdict"], parsed["reason"], parsed["scores"], "llm")
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("judge_unavailable step=%s error=%s", step_name, exc)
        fallback = rule_based_validate(output=text, step_description=step_description)
        return Judgement(str(fallback["verdict"]), str(fallback["reason"]), {}, "rules")


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


def _get_validation_model() -> str:
    """Resolve configured validation model with fallback to a free open-source model."""
    settings = get_settings()
    configured = getattr(settings, "VALIDATION_MODEL", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return settings.primary_model

