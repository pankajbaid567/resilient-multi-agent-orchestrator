"""LLM-native metrics computed from per-call usage records (state["llm_calls"]).

Every number here comes from what providers reported for real calls in the run: token
counts, latency, model. Cost is reported only for models with a configured price
(MODEL_PRICES); otherwise ``cost_usd`` is None rather than a guess.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


def model_prices() -> dict[str, tuple[float, float]]:
    """Parse MODEL_PRICES ({"model": [usd_per_1M_in, usd_per_1M_out]}); invalid entries are ignored."""
    raw = (get_settings().MODEL_PRICES or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("MODEL_PRICES is not valid JSON; costs will not be reported")
        return {}
    prices: dict[str, tuple[float, float]] = {}
    for model, value in parsed.items() if isinstance(parsed, dict) else []:
        try:
            prices[str(model)] = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return prices


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-q * len(ordered) // 100))))
    return float(ordered[rank - 1])


def count_fallbacks(records: list[dict]) -> tuple[int, int]:
    """Return (fallbacks, same_model_retries) inferred from call order within each (node, step).

    A failed call followed by a call on a different model is a fallback; followed by the same
    model it is a retry.
    """
    fallbacks = retries = 0
    last_failed: dict[tuple, str] = {}
    for record in records:
        key = (record.get("node"), record.get("step_id"))
        model = str(record.get("model") or "")
        if key in last_failed:
            if last_failed[key] == model:
                retries += 1
            else:
                fallbacks += 1
        if record.get("ok"):
            last_failed.pop(key, None)
        else:
            last_failed[key] = model
    return fallbacks, retries


def summarize_llm_calls(records: list[dict] | None) -> dict[str, Any]:
    """Aggregate call records into totals, per-node/per-model breakdowns, latency and cost."""
    records = list(records or [])
    ok_records = [r for r in records if r.get("ok")]
    input_tokens = sum(int(r.get("input_tokens") or 0) for r in records)
    output_tokens = sum(int(r.get("output_tokens") or 0) for r in records)
    latencies = [float(r.get("latency_ms") or 0) for r in ok_records]
    ok_latency_s = sum(latencies) / 1000.0
    ok_output = sum(int(r.get("output_tokens") or 0) for r in ok_records)

    by_node: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    for record in records:
        tokens = int(record.get("input_tokens") or 0) + int(record.get("output_tokens") or 0)
        for bucket, key in ((by_node, str(record.get("node") or "unknown")), (by_model, str(record.get("model") or ""))):
            entry = bucket.setdefault(key, {"calls": 0, "failed": 0, "tokens": 0})
            entry["calls"] += 1
            entry["failed"] += 0 if record.get("ok") else 1
            entry["tokens"] += tokens

    prices = model_prices()
    cost: float | None = None
    unpriced = sorted({str(r.get("model")) for r in ok_records if str(r.get("model")) not in prices})
    if prices and not unpriced:
        cost = round(sum(
            int(r.get("input_tokens") or 0) / 1e6 * prices[str(r["model"])][0]
            + int(r.get("output_tokens") or 0) / 1e6 * prices[str(r["model"])][1]
            for r in ok_records), 6)

    fallbacks, retries = count_fallbacks(records)
    return {
        "calls": len(records),
        "failed_calls": len(records) - len(ok_records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95),
                       "max": max(latencies) if latencies else 0.0},
        "output_tokens_per_second": round(ok_output / ok_latency_s, 2) if ok_latency_s > 0 else 0.0,
        "fallbacks": fallbacks,
        "provider_retries": retries,
        "by_node": by_node,
        "by_model": by_model,
        "cost_usd": cost,
        "unpriced_models": unpriced,
    }
