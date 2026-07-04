"""Planner node responsible for decomposing a user task into executable steps."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from time import perf_counter
from typing import Any, List

from agent.state import AgentState
from config import get_settings
from models import StepDefinition, TraceEntry
from services.llm_service import LLMError, call_llm

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are an expert task planner. Your job is to break down complex tasks into clear, sequential, executable steps.

You must return a valid JSON array of step objects. Each step object must include these exact fields:
- step_id
- name
- description
- tool_needed
- dependencies
- estimated_complexity

Field constraints:
- step_id format: step_1, step_2, step_3, ...
- tool_needed must be one of: web_search, api_call, code_exec, llm_only, none
- dependencies must be an array of step_id values that appear before the current step
- estimated_complexity must be one of: low, medium, high

Dependency analysis requirements:
- For each step, list its dependencies - which prior steps MUST complete before this step can start.
- If a step has no dependencies (can run independently), set dependencies to an empty list.
- Maximize parallelism - only add dependencies where data from a prior step is actually needed.

Few-shot example 0:
Task: "Research AI and crypto, then compare them"
[
    {
        "step_id": "step_1",
        "name": "Research AI",
        "description": "Collect recent developments, use-cases, and trends in AI from reliable sources.",
        "tool_needed": "web_search",
        "dependencies": [],
        "estimated_complexity": "medium"
    },
    {
        "step_id": "step_2",
        "name": "Research crypto",
        "description": "Collect recent developments, market themes, and major events in crypto from reliable sources.",
        "tool_needed": "web_search",
        "dependencies": [],
        "estimated_complexity": "medium"
    },
    {
        "step_id": "step_3",
        "name": "Compare AI and crypto",
        "description": "Compare AI and crypto findings across opportunity, risk, and long-term outlook.",
        "tool_needed": "llm_only",
        "dependencies": ["step_1", "step_2"],
        "estimated_complexity": "high"
    }
]

Few-shot example 1:
User task: "Research the latest AI trends and write a summary"
Good decomposition (4 steps: search -> extract key findings -> analyze trends -> write summary):
[
  {
    "step_id": "step_1",
    "name": "Search for latest AI trends",
    "description": "Use reliable sources to collect recent AI trend reports, articles, and industry updates from the last 12 months.",
    "tool_needed": "web_search",
    "dependencies": [],
    "estimated_complexity": "medium"
  },
  {
    "step_id": "step_2",
    "name": "Extract key findings",
    "description": "Identify and organize the most important findings, statistics, and announcements from the collected sources.",
    "tool_needed": "llm_only",
    "dependencies": ["step_1"],
    "estimated_complexity": "medium"
  },
  {
    "step_id": "step_3",
    "name": "Analyze trend significance",
    "description": "Evaluate which trends are short-term vs long-term and explain their likely impact on businesses and developers.",
    "tool_needed": "llm_only",
    "dependencies": ["step_2"],
    "estimated_complexity": "high"
  },
  {
    "step_id": "step_4",
    "name": "Write concise summary",
    "description": "Produce a clear, structured summary of the most important AI trends and their implications.",
    "tool_needed": "llm_only",
    "dependencies": ["step_3"],
    "estimated_complexity": "medium"
  }
]

Few-shot example 2:
User task: "Compare prices of 3 products and recommend the best"
Good decomposition (3 steps: search for products -> compile comparison -> generate recommendation):
[
  {
    "step_id": "step_1",
    "name": "Search product options and prices",
    "description": "Find three relevant product options and collect current prices and key specs from trustworthy sources.",
    "tool_needed": "web_search",
    "dependencies": [],
    "estimated_complexity": "medium"
  },
  {
    "step_id": "step_2",
    "name": "Compile structured comparison",
    "description": "Create a comparison of price, major features, pros, and cons for the three products.",
    "tool_needed": "llm_only",
    "dependencies": ["step_1"],
    "estimated_complexity": "medium"
  },
  {
    "step_id": "step_3",
    "name": "Generate recommendation",
    "description": "Recommend the best option based on value-for-money and explain the rationale in plain language.",
    "tool_needed": "llm_only",
    "dependencies": ["step_2"],
    "estimated_complexity": "low"
  }
]

Return only valid JSON. Do not include markdown fences, prose, or extra keys."""

PLANNER_USER_PROMPT_TEMPLATE = (
    "Decompose this task into 2-10 executable steps with explicit dependencies. "
    "Maximize parallelism where possible: {original_input}"
)

STRICT_JSON_SUFFIX = (
    "Your previous output could not be parsed as valid JSON. "
    "Respond with ONLY a valid JSON array of step objects. "
    "Do not add markdown, commentary, or any text outside the JSON array."
)


def validate_step_order(steps: List[StepDefinition]) -> List[StepDefinition]:
    """Verify and fix dependency ordering. Returns reordered steps if needed."""
    if len(steps) <= 1:
        return steps

    step_ids = {step.step_id for step in steps}
    sanitized_by_id: dict[str, StepDefinition] = {}
    original_order = [step.step_id for step in steps]

    for step in steps:
        deps: list[str] = []
        for dependency in step.dependencies:
            if dependency in step_ids and dependency != step.step_id and dependency not in deps:
                deps.append(dependency)
        sanitized_by_id[step.step_id] = step.model_copy(update={"dependencies": deps})

    indegree = {step_id: 0 for step_id in original_order}
    adjacency: dict[str, list[str]] = {step_id: [] for step_id in original_order}

    for step in original_order:
        for dependency in sanitized_by_id[step].dependencies:
            adjacency[dependency].append(step)
            indegree[step] += 1

    queue = [step_id for step_id in original_order if indegree[step_id] == 0]
    ordered_ids: list[str] = []

    while queue:
        current = queue.pop(0)
        ordered_ids.append(current)
        for follower in adjacency[current]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)

    if len(ordered_ids) == len(original_order):
        return [sanitized_by_id[step_id] for step_id in ordered_ids]

    logger.warning("planner_dependency_cycle_detected; applying sequential fallback ordering")
    repaired: list[StepDefinition] = []
    for index, step_id in enumerate(original_order):
        deps = [] if index == 0 else [repaired[index - 1].step_id]
        repaired.append(sanitized_by_id[step_id].model_copy(update={"dependencies": deps}))
    return repaired


async def planner_node(state: AgentState) -> AgentState:
    """Decomposes user's task into 2-10 executable steps using LLM."""
    planning_started = perf_counter()
    state["status"] = "planning"

    try:
        if state.get("steps"):
            state["status"] = "executing"
            return state

        parse_errors: list[str] = []
        llm_errors: list[str] = []
        llm_success_count = 0
        total_tokens = 0
        model_used = ""
        parsed_steps: list[StepDefinition] | None = None
        planner_model = _get_planner_model()
        planner_provider = _provider_for_model(planner_model)

        for attempt in range(3):
            strict_mode = attempt > 0
            user_prompt = _build_user_prompt(state["original_input"], strict_mode=strict_mode)

            try:
                response = await call_llm(
                    prompt=user_prompt,
                    system_prompt=PLANNER_SYSTEM_PROMPT,
                    model=planner_model,
                    provider=planner_provider,
                    temperature=0.2,
                    max_tokens=4096,
                    json_mode=True,
                    timeout=60,
                )
                llm_success_count += 1
                total_tokens += response.tokens_used
                state["llm_tokens_used"] += response.tokens_used
                model_used = response.model_used
            except LLMError as exc:
                llm_errors.append(str(exc))
                continue

            try:
                parsed_steps = _parse_steps_from_llm_response(response.text)
                break
            except (ValueError, json.JSONDecodeError) as exc:
                parse_errors.append(str(exc))

        if parsed_steps is None:
            if llm_success_count == 0:
                state["status"] = "failed"
                state["error_log"].append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "step_id": "step_0",
                        "error_type": "PLANNER_LLM_FAILURE",
                        "error_message": "All planner LLM calls failed.",
                        "details": {"errors": llm_errors},
                    }
                )
                return state

            logger.warning("planner_json_parse_failed; using fallback plan parse_errors=%s", parse_errors)
            parsed_steps = _build_fallback_steps()
            state["error_log"].append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "step_id": "step_0",
                    "error_type": "PLANNER_PARSE_FALLBACK",
                    "error_message": "Planner response was not valid JSON after retries. Applied fallback steps.",
                    "details": {"parse_errors": parse_errors},
                }
            )

        steps = validate_step_order(parsed_steps)

        if len(steps) > 10:
            logger.warning("planner_step_count_truncated original=%s truncated=10", len(steps))
            state["error_log"].append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "step_id": "step_0",
                    "error_type": "PLANNER_TRUNCATED",
                    "error_message": f"Planner returned {len(steps)} steps; truncated to 10.",
                }
            )
            steps = steps[:10]
            allowed_ids = {step.step_id for step in steps}
            steps = [
                step.model_copy(
                    update={
                        "dependencies": [
                            dependency
                            for dependency in step.dependencies
                            if dependency in allowed_ids and dependency != step.step_id
                        ]
                    }
                )
                for step in steps
            ]
            steps = validate_step_order(steps)

        if len(steps) == 1:
            review_step = StepDefinition(
                step_id="step_2",
                name="Review and finalize",
                description="Review the previous result for completeness and finalize the response.",
                tool_needed="llm_only",
                dependencies=[steps[0].step_id],
                estimated_complexity="low",
            )
            steps.append(review_step)

        if len(steps) < 2:
            steps = _build_fallback_steps()

        steps = _renumber_steps(steps)
        steps = validate_step_order(steps)

        planning_latency_ms = int((perf_counter() - planning_started) * 1000)
        state["steps"] = steps
        state["status"] = "executing"
        state["execution_trace"].append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="planning_complete",
                details={
                    "step_count": len(steps),
                    "source": "llm" if llm_success_count > 0 else "fallback",
                },
                duration_ms=planning_latency_ms,
                tokens_used=total_tokens,
                model_used=model_used or "",
            ).model_dump()
        )

        logger.info(
            "planner_complete steps=%s planning_time_ms=%s model=%s",
            len(steps),
            planning_latency_ms,
            model_used or "fallback",
        )
        return state
    except Exception as exc:  # pragma: no cover - defensive non-throwing behavior
        logger.exception("planner_node_unhandled_error: %s", exc)
        state["status"] = "failed"
        state["error_log"].append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step_id": "step_0",
                "error_type": "PLANNER_UNHANDLED_ERROR",
                "error_message": str(exc),
            }
        )
        return state


def _build_user_prompt(original_input: str, strict_mode: bool) -> str:
    """Build user prompt with optional strict JSON recovery instructions."""
    prompt = PLANNER_USER_PROMPT_TEMPLATE.format(original_input=original_input)
    if strict_mode:
        prompt = f"{prompt}\n\n{STRICT_JSON_SUFFIX}"
    return prompt


def _parse_steps_from_llm_response(raw_text: str) -> list[StepDefinition]:
    """Parse and normalize planner JSON output into StepDefinition objects."""
    payload = _extract_json_payload(raw_text)
    data = _load_json_payload(payload)

    raw_steps: list[Any]
    if isinstance(data, list):
        raw_steps = data
    elif isinstance(data, dict) and isinstance(data.get("steps"), list):
        raw_steps = data["steps"]
    else:
        raise ValueError("Planner response must be a JSON array or an object with a 'steps' array")

    steps = _coerce_steps(raw_steps)
    if not steps:
        raise ValueError("Planner response did not contain valid step objects")
    return steps


def _extract_json_payload(raw_text: str) -> str:
    """Extract a JSON payload from plain text or markdown-fenced model output."""
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def _load_json_payload(payload: str) -> Any:
    """Load JSON payload with fallback slicing for wrapped output."""
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        array_start = payload.find("[")
        array_end = payload.rfind("]")
        if array_start != -1 and array_end != -1 and array_end > array_start:
            return json.loads(payload[array_start : array_end + 1])

        object_start = payload.find("{")
        object_end = payload.rfind("}")
        if object_start != -1 and object_end != -1 and object_end > object_start:
            return json.loads(payload[object_start : object_end + 1])
        raise


def _coerce_steps(raw_steps: list[Any]) -> list[StepDefinition]:
    """Normalize raw step objects from the LLM into validated StepDefinition models."""
    prepared: list[tuple[str, dict[str, Any]]] = []
    id_map: dict[str, str] = {}
    used_ids: set[str] = set()

    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            continue

        original_step_id = str(item.get("step_id", "")).strip()
        step_id = _normalize_step_id(original_step_id, fallback_index=index)
        while step_id in used_ids:
            step_id = f"step_{len(used_ids) + 1}"

        used_ids.add(step_id)
        if original_step_id:
            id_map[original_step_id] = step_id
        id_map[str(index)] = step_id
        prepared.append((step_id, item))

    if not prepared:
        return []

    valid_ids = {step_id for step_id, _ in prepared}
    results: list[StepDefinition] = []
    for index, (step_id, item) in enumerate(prepared, start=1):
        name = str(item.get("name") or f"Step {index}").strip()
        description = str(item.get("description") or name).strip()
        tool_needed = _normalize_tool_needed(item.get("tool_needed"))
        dependencies = _normalize_dependencies(
            raw_dependencies=item.get("dependencies"),
            current_step_id=step_id,
            id_map=id_map,
            valid_ids=valid_ids,
        )
        estimated_complexity = _normalize_complexity(item.get("estimated_complexity"))

        results.append(
            StepDefinition(
                step_id=step_id,
                name=name,
                description=description,
                tool_needed=tool_needed,
                dependencies=dependencies,
                estimated_complexity=estimated_complexity,
            )
        )

    return results


def _normalize_step_id(value: str, fallback_index: int) -> str:
    """Normalize step IDs to canonical step_<n> format."""
    normalized = value.strip().lower()
    if re.fullmatch(r"step_\d+", normalized):
        return normalized

    dash_match = re.fullmatch(r"step-(\d+)", normalized)
    if dash_match:
        return f"step_{int(dash_match.group(1))}"

    numeric_match = re.fullmatch(r"(\d+)", normalized)
    if numeric_match:
        return f"step_{int(numeric_match.group(1))}"

    generic_match = re.search(r"(\d+)", normalized)
    if generic_match:
        return f"step_{int(generic_match.group(1))}"

    return f"step_{fallback_index}"


def _normalize_tool_needed(value: Any) -> str:
    """Normalize tool labels into the supported planner tool set."""
    normalized = str(value or "").strip().lower()
    allowed = {"web_search", "api_call", "code_exec", "llm_only", "none"}
    if normalized in allowed:
        return normalized
    if normalized in {"llm", "llm-call", "language_model"}:
        return "llm_only"
    return "llm_only"


def _normalize_complexity(value: Any) -> str:
    """Normalize complexity labels to low/medium/high."""
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return "medium"


def _normalize_dependencies(
    raw_dependencies: Any,
    current_step_id: str,
    id_map: dict[str, str],
    valid_ids: set[str],
) -> list[str]:
    """Normalize dependency references and drop invalid or circular entries."""
    if not isinstance(raw_dependencies, list):
        return []

    dependencies: list[str] = []
    for dependency in raw_dependencies:
        dep_raw = str(dependency).strip()
        if not dep_raw:
            continue

        normalized = id_map.get(dep_raw)
        if normalized is None:
            normalized = _normalize_step_id(dep_raw, fallback_index=0)

        if normalized in valid_ids and normalized != current_step_id and normalized not in dependencies:
            dependencies.append(normalized)

    return dependencies


def _renumber_steps(steps: list[StepDefinition]) -> list[StepDefinition]:
    """Renumber step IDs sequentially and remap dependencies accordingly."""
    id_mapping: dict[str, str] = {}
    for index, step in enumerate(steps, start=1):
        id_mapping[step.step_id] = f"step_{index}"

    renumbered: list[StepDefinition] = []
    for step in steps:
        new_step_id = id_mapping[step.step_id]
        deps = [id_mapping[dependency] for dependency in step.dependencies if dependency in id_mapping]
        renumbered.append(step.model_copy(update={"step_id": new_step_id, "dependencies": deps}))

    return renumbered


def _build_fallback_steps() -> list[StepDefinition]:
    """Build a deterministic fallback plan when JSON planning repeatedly fails."""
    return [
        StepDefinition(
            step_id="step_1",
            name="Research relevant information",
            description="Gather reliable information and key data needed to address the task.",
            tool_needed="web_search",
            dependencies=[],
            estimated_complexity="medium",
        ),
        StepDefinition(
            step_id="step_2",
            name="Analyze findings",
            description="Analyze the gathered information to identify important patterns and actionable insights.",
            tool_needed="llm_only",
            dependencies=["step_1"],
            estimated_complexity="medium",
        ),
        StepDefinition(
            step_id="step_3",
            name="Synthesize final output",
            description="Synthesize the analysis into a clear, complete final response for the user.",
            tool_needed="llm_only",
            dependencies=["step_2"],
            estimated_complexity="low",
        ),
    ]


def _get_planner_model() -> str:
    """Resolve planner model from runtime settings with a safe open-source default."""
    settings = get_settings()
    configured = str(getattr(settings, "PRIMARY_MODEL", "") or "").strip()
    if configured:
        return configured
    return "meta-llama/Llama-3.1-8B-Instruct"


def _provider_for_model(model_name: str) -> str:
    """Infer provider label from model naming while preferring open-source routing."""
    normalized = model_name.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gpt"):
        return "openai"
    return "open_source"
