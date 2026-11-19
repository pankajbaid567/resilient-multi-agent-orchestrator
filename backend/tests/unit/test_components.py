"""Pure components: planner parsing, dependency graph, judge parsing, backoff, reflection parsing."""

from __future__ import annotations

import pytest

from agent.nodes.planner import _parse_steps_from_llm_response, _renumber_steps, validate_step_order
from agent.nodes.reflector import _parse_reflection_response
from agent.nodes.validator import _derive_verdict_from_scores, _parse_validation_response, rule_based_validate
from agent.parallel.dag import ExecutionDAG
from agent.reliability.retry import compute_backoff_seconds
from models import StepDefinition


def _s(step_id: str, deps: list[str]) -> StepDefinition:
    return StepDefinition(step_id=step_id, name=step_id, description="d", tool_needed="llm_only",
                          dependencies=deps, estimated_complexity="low")


# ---- planner -----------------------------------------------------------------------------

def test_planner_reads_fenced_json_with_prose_around_it():
    raw = 'Here is the plan:\n```json\n[{"step_id": "step_1", "name": "A", "description": "a", ' \
          '"tool_needed": "LLM", "dependencies": []}]\n```\nDone.'
    steps = _parse_steps_from_llm_response(raw)
    assert [s.step_id for s in steps] == ["step_1"] and steps[0].tool_needed == "llm_only"


def test_planner_accepts_object_with_steps_key_and_drops_unknown_dependencies():
    raw = '{"steps": [{"name": "A", "description": "a", "tool_needed": "web_search", "dependencies": ["step_9"]},' \
          '{"name": "B", "description": "b", "tool_needed": "none", "dependencies": ["1"]}]}'
    steps = _parse_steps_from_llm_response(raw)
    assert steps[0].dependencies == [] and steps[1].dependencies == ["step_1"]


def test_planner_rejects_non_plan_output():
    with pytest.raises(ValueError):
        _parse_steps_from_llm_response('{"answer": 42}')


def test_cycle_is_repaired_into_a_sequential_chain():
    repaired = validate_step_order([_s("step_1", ["step_2"]), _s("step_2", ["step_1"])])
    assert [(s.step_id, s.dependencies) for s in repaired] == [("step_1", []), ("step_2", ["step_1"])]


def test_valid_order_is_topologically_sorted():
    ordered = validate_step_order([_s("step_1", ["step_2"]), _s("step_2", [])])
    assert [s.step_id for s in ordered] == ["step_2", "step_1"]


def test_renumbering_remaps_dependencies():
    steps = _renumber_steps([_s("step_4", []), _s("step_7", ["step_4"])])
    assert [(s.step_id, s.dependencies) for s in steps] == [("step_1", []), ("step_2", ["step_1"])]


# ---- dependency graph --------------------------------------------------------------------

def test_dag_levels_group_independent_steps():
    dag = ExecutionDAG.from_steps([_s("step_1", []), _s("step_2", []), _s("step_3", ["step_1", "step_2"])])
    assert dag.validate() == (True, None)
    assert dag.get_execution_levels() == [["step_1", "step_2"], ["step_3"]]


def test_dag_rejects_duplicates_and_missing_dependencies():
    with pytest.raises(ValueError):
        ExecutionDAG.from_steps([_s("step_1", []), _s("step_1", [])])
    ok, error = ExecutionDAG.from_steps([_s("step_1", ["step_5"])]).validate()
    assert not ok and "step_5" in error


# ---- judge -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("scores", "verdict"),
    [({"a": 6, "b": 9}, "pass"), ({"a": 5, "b": 9}, "retry"), ({"a": 2, "b": 9}, "reflect"), ({}, "retry")],
)
def test_verdict_follows_the_score_rules(scores, verdict):
    assert _derive_verdict_from_scores(scores) == verdict


def test_judge_verdict_is_derived_from_scores_not_trusted():
    raw = '{"verdict": "pass", "reason": "fine", "scores": {"relevance": 2, "completeness": 9, ' \
          '"consistency": 9, "plausibility": 9}}'
    assert _parse_validation_response(raw)["verdict"] == "reflect"


def test_scores_are_clamped_and_garbage_is_zero():
    raw = '{"verdict": "retry", "reason": "x", "scores": {"relevance": 99, "completeness": "n/a", ' \
          '"consistency": -3, "plausibility": 7}}'
    scores = _parse_validation_response(raw)["scores"]
    assert scores == {"relevance": 10, "completeness": 0, "consistency": 0, "plausibility": 7}


def test_rule_based_fallback_needs_length_and_keywords():
    assert rule_based_validate("short", "describe solar panels")["verdict"] == "retry"
    long_text = "Solar panels convert sunlight; efficiency of modern panels is about twenty percent today."
    assert rule_based_validate(long_text, "describe solar panels efficiency")["verdict"] == "pass"


# ---- backoff and reflection parsing ------------------------------------------------------

def test_backoff_grows_exponentially_and_is_capped():
    for attempt in range(6):
        delay = compute_backoff_seconds(attempt, base_seconds=1.0, max_seconds=30.0)
        assert 2**attempt <= delay <= min(2**attempt + 1, 30.0) or delay == 30.0
    assert compute_backoff_seconds(20, base_seconds=1.0, max_seconds=30.0) == 30.0


def test_reflection_parsing_accepts_python_dict_and_falls_back_to_keywords():
    parsed = _parse_reflection_response("{'action': 'modify', 'reasoning': 'r', 'modified_step': 'new'}")
    assert parsed["action"] == "MODIFY_STEP" and parsed["modified_step"] == "new"
    assert _parse_reflection_response("I think we should DECOMPOSE this.")["action"] == "DECOMPOSE"
    assert _parse_reflection_response("no idea")["action"] == "SKIP_STEP"


def test_state_reducers_upsert_steps_and_keep_first_abort_reason():
    from agent.state import first_reason, merge_dict, merge_steps

    updated = _s("step_1", []).model_copy(update={"description": "rewritten"})
    merged = merge_steps([_s("step_1", []), _s("step_2", ["step_1"])], [updated, _s("step_3", [])])
    assert [s.step_id for s in merged] == ["step_1", "step_2", "step_3"]
    assert merged[0].description == "rewritten"
    assert merge_dict({"a": 1, "b": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert first_reason(None, "x") == "x" and first_reason("first", "second") == "first"


@pytest.mark.parametrize(
    "raw",
    ['{\n{"verdict": "pass", "scores": {"a": 9}}', 'Here you go:\n```json\n{"verdict": "pass"}\n```',
     'noise {not json} then {"verdict": "pass"} trailing'],
)
def test_json_extraction_survives_provider_artefacts(raw):
    from services.json_extract import extract_json_object

    assert extract_json_object(raw)["verdict"] == "pass"


def test_judge_parses_doubled_brace_output():
    raw = '{\n{"verdict":"pass","reason":"ok","scores":{"relevance":9,"completeness":8,"consistency":9,"plausibility":9}}}'
    assert _parse_validation_response(raw)["verdict"] == "pass"
