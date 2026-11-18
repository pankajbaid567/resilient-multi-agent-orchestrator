from agent.graph import run_agent

async def test_parallel_and_multiagent_smoke(fake_llm, settings):
    settings.PARALLEL_MODE = True
    settings.MULTI_AGENT_MODE = True
    final = await run_agent("p1", "Summarise solar panel efficiency")
    assert final["status"] == "completed", final["error_log"]
    assert {r.step_id for r in final["step_results"]} == {"step_1", "step_2"}

async def test_sequential_multiagent_smoke(fake_llm, settings):
    settings.MULTI_AGENT_MODE = True
    final = await run_agent("p2", "Summarise solar panel efficiency")
    assert final["status"] == "completed", final["error_log"]
