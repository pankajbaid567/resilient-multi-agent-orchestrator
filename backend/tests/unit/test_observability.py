"""LLM usage metrics and OpenTelemetry spans."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.graph import run_agent
from services.llm_metrics import count_fallbacks, percentile, summarize_llm_calls
from tests.unit.fakes import provider_error

_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture
def spans():
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def _rec(node, model, ok=True, tin=10, tout=5, ms=100, step="step_1"):
    return {"node": node, "model": model, "ok": ok, "input_tokens": tin if ok else 0,
            "output_tokens": tout if ok else 0, "latency_ms": ms, "step_id": step}


def test_percentile_nearest_rank():
    assert percentile([], 50) == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2.0
    assert percentile([1, 2, 3, 4], 95) == 4.0


def test_fallback_and_retry_are_inferred_from_call_order():
    records = [_rec("execute", "a", ok=False), _rec("execute", "a", ok=False), _rec("execute", "b"),
               _rec("validate", "a", ok=False), _rec("validate", "a")]
    assert count_fallbacks(records) == (1, 2)


def test_summary_reports_tokens_latency_and_throughput():
    summary = summarize_llm_calls([_rec("execute", "a", ms=200, tout=50), _rec("validate", "a", ms=100, tout=10),
                                   _rec("execute", "b", ok=False, ms=40)])
    assert (summary["calls"], summary["failed_calls"]) == (3, 1)
    assert (summary["input_tokens"], summary["output_tokens"]) == (20, 60)
    assert summary["latency_ms"]["p50"] == 100.0 and summary["latency_ms"]["max"] == 200.0
    assert summary["output_tokens_per_second"] == 200.0  # 60 tokens / 0.3 s of successful calls
    assert summary["by_node"]["execute"] == {"calls": 2, "failed": 1, "tokens": 60}


def test_cost_only_when_every_used_model_is_priced(settings):
    records = [_rec("execute", "a", tin=1_000_000, tout=1_000_000), _rec("execute", "b")]
    assert summarize_llm_calls(records)["cost_usd"] is None
    settings.MODEL_PRICES = '{"a": [0.1, 0.4]}'
    unpriced = summarize_llm_calls(records)
    assert unpriced["cost_usd"] is None and unpriced["unpriced_models"] == ["b"]
    settings.MODEL_PRICES = '{"a": [0.1, 0.4], "b": [0, 0]}'
    assert summarize_llm_calls(records)["cost_usd"] == pytest.approx(0.5)


async def test_run_records_fallback_in_final_summary(fake_llm):
    fake_llm.script("executor", provider_error(400))  # first worker call rejected on the primary model
    final = await run_agent("obs-1", "Summarise solar panel efficiency")
    usage = final["final_output"]["summary"]["llm_usage"]
    assert usage["failed_calls"] == 1 and usage["fallbacks"] == 1
    assert usage["calls"] == len(final["llm_calls"])


async def test_spans_cover_task_steps_agents_and_model_calls(fake_llm, spans):
    await run_agent("obs-2", "Summarise solar panel efficiency")
    finished = spans.get_finished_spans()
    names = [s.name for s in finished]
    assert "task.execute" in names and "step step_1" in names and "agent analysis" in names
    chat = [s for s in finished if s.name.startswith("chat ")]
    assert chat and all("gen_ai.usage.output_tokens" in s.attributes for s in chat)
    by_id = {s.context.span_id: s for s in finished}
    worker_chat = next(s for s in chat if s.parent and by_id.get(s.parent.span_id, None)
                       and by_id[s.parent.span_id].name == "agent analysis")
    agent_span = by_id[worker_chat.parent.span_id]
    step_span = by_id[agent_span.parent.span_id]
    assert step_span.name.startswith("step ")
    assert by_id[step_span.parent.span_id].name == "task.execute"
