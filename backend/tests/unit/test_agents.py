"""Supervisor delegation and the ReAct worker loop."""

from __future__ import annotations

from agent.multi_agent.agents import AGENT_REGISTRY
from agent.multi_agent.supervisor import delegate, rule_based_role
from agent.multi_agent.worker import parse_reply, run_worker
from models import StepDefinition


def _step(description: str, tool: str = "llm_only") -> StepDefinition:
    return StepDefinition(step_id="step_1", name="Step", description=description, tool_needed=tool,
                          dependencies=[], estimated_complexity="low")


def test_parse_reply_ignores_observations_the_model_invented():
    kind, tool, arg = parse_reply("Action: web_search\nAction Input: solar\nObservation: made-up results")
    assert (kind, tool, arg) == ("action", "web_search", "solar")


def test_parse_reply_reads_final_answer_and_plain_text():
    assert parse_reply("Thought: done\nFinal Answer: 42 is the answer")[:2] == ("final", "42 is the answer")
    assert parse_reply("Just an answer without markers")[:2] == ("plain", "Just an answer without markers")


def test_rules_use_tool_hint_then_keywords():
    assert rule_based_role(_step("anything", "web_search"))[0] == "research"
    assert rule_based_role(_step("Compare the two options and evaluate trade-offs"))[0] == "analysis"
    assert rule_based_role(_step("Summarize the findings in a short report"))[0] == "writing"


async def test_unusable_supervisor_reply_falls_back_to_rules(fake_llm):
    fake_llm.script("supervisor", {"agent": "astrologer", "brief": "read the stars"})
    delegation = await delegate(_step("Summarize the findings in a short report"), "task")
    assert (delegation.role, delegation.source) == ("writing", "rules")
    assert delegation.brief == "Summarize the findings in a short report"


async def test_rules_mode_makes_no_model_call(fake_llm, settings):
    settings.SUPERVISOR_MODE = "rules"
    await delegate(_step("Compare options"), "task")
    assert fake_llm.calls_for("supervisor") == []


async def test_worker_reports_unknown_tool_and_continues(fake_llm, settings):
    settings.TAVILY_API_KEY = "k"
    fake_llm.script("executor", "Action: teleport\nAction Input: mars", "Final Answer: Done without that tool, details here.")
    result = await run_worker(AGENT_REGISTRY["research"], _step("find facts"), "task", "brief", "", [])
    assert result.ok and result.output.startswith("Done without that tool")
    assert result.tool_calls[0].tool == "teleport" and not result.tool_calls[0].ok
    assert "Unknown tool 'teleport'" in fake_llm.calls_for("executor")[1]["prompt"]


async def test_worker_stops_calling_tools_after_the_limit(fake_llm, settings, monkeypatch):
    from agent.tools import registry
    from agent.tools.web_search import ToolResult

    async def search(query, max_results=5):
        return ToolResult(True, {"query": query, "results": [{"title": "t", "url": "u", "content": "c"}]}, "", 1, "web_search")

    monkeypatch.setattr(registry, "web_search", search)
    settings.TAVILY_API_KEY = "k"
    settings.MAX_AGENT_TOOL_CALLS = 2
    fake_llm.script("executor", *["Action: web_search\nAction Input: q"] * 3, "Final Answer: summary of the searches.")
    result = await run_worker(AGENT_REGISTRY["research"], _step("find facts"), "task", "brief", "", [])
    assert len(result.tool_calls) == 2
    assert "You cannot call more tools" in fake_llm.calls_for("executor")[2]["prompt"]
    assert result.ok and result.output == "summary of the searches."  # one extra round to get the answer


async def test_tool_less_roles_get_no_tool_protocol(fake_llm):
    await run_worker(AGENT_REGISTRY["writing"], _step("write"), "task", "brief", "", [])
    system = fake_llm.calls_for("executor")[0]["system"]
    assert "Action:" not in system and "Final Answer:" in system
