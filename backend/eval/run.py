"""Orchestration eval: three systems x three fault profiles over 40 tasks.

    cd backend && python -m eval.run                       # simulated provider (default)
    cd backend && python -m eval.run --seeds 1 2 3         # more seeds
    cd backend && python -m eval.run --live --limit 10     # real provider from .env (costs tokens)

Systems
  naive       one model call per task: no plan, no validation, no retries, no fallback
  retry_only  full graph, validation and retries, but one model (no fallback), no circuit
              breaker and no reflection
  full        full graph: retries, model fallback, circuit breaker, reflection

Profiles (simulated)
  clean   no injected faults; 20% of worker answers are low quality
  chaos   the app's chaos middleware on (latency, empty, rate limit, corrupt), plus the 20%
  outage  the primary model returns 503 on every call, plus the 20%

Writes eval/results/<label>.jsonl (one line per run) and eval/results/<label>.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
SIM_ENV = {
    "OPEN_SOURCE_API_KEY": "sim", "OPEN_SOURCE_BASE_URL": "http://simulated.invalid/v1", "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "", "TAVILY_API_KEY": "", "REDIS_URL": "redis://127.0.0.1:1", "LOG_LEVEL": "ERROR",
    "SUPERVISOR_MODE": "llm", "PARALLEL_MODE": "true", "MAX_RETRIES": "2", "CHECKPOINT_URL": "memory",
    "PRIMARY_MODEL": "sim-primary", "FALLBACK_MODEL": "sim-secondary", "VALIDATION_MODEL": "sim-primary",
    "TASK_TOKEN_BUDGET": "0", "TASK_DEADLINE_SECONDS": "0",
}
SYSTEMS = ("naive", "retry_only", "full")
PROFILES = ("clean", "chaos", "outage")
FULL_BODY_END = "clear conclusion for this part of the task."


def load_tasks(limit: int | None) -> list[dict]:
    tasks = [json.loads(line) for line in (HERE / "tasks.jsonl").read_text().splitlines() if line.strip()]
    return tasks[:limit] if limit else tasks


def configure(system: str, profile: str, live: bool) -> None:
    from agent.graph import reset_graph
    from agent.reliability import chaos, circuit_breaker
    from config import get_settings
    from services import redis_service

    get_settings.cache_clear()
    reset_graph()
    circuit_breaker._manager = None
    chaos._chaos_middleware = None
    redis_service._redis_service = None
    settings = get_settings()
    if not live:
        for key in ("PRIMARY_MODEL", "FALLBACK_MODEL", "VALIDATION_MODEL", "SUPERVISOR_MODE", "TASK_TOKEN_BUDGET",
                    "TASK_DEADLINE_SECONDS", "MAX_RETRIES", "PARALLEL_MODE", "TAVILY_API_KEY"):
            field_type = type(getattr(settings, key))
            value = SIM_ENV[key]
            setattr(settings, key, value.lower() == "true" if field_type is bool else field_type(value))
        settings.MODEL_CHAIN = "sim-primary" if system == "retry_only" else "sim-primary,sim-secondary,sim-tertiary"
    settings.ENABLE_REFLECTION = system == "full"
    settings.ENABLE_CIRCUIT_BREAKER = system == "full"
    settings.CHAOS_MODE = profile == "chaos"


def good_output(text: str) -> bool:
    """Ground truth in simulation: not marked low quality and not truncated by chaos corruption."""
    from eval.simulator import LOW_QUALITY

    return bool(text) and LOW_QUALITY not in text and FULL_BODY_END in text


async def run_naive(task: dict, task_id: str) -> dict:
    from agent.reliability.chaos import get_chaos_middleware
    from config import get_settings
    from services.llm_service import LLMError, call_llm
    from services.usage import usage_scope

    output, error = "", None
    with usage_scope(node="naive") as calls:
        try:
            injection = None
            if get_settings().CHAOS_MODE:
                middleware = get_chaos_middleware()
                middleware.enabled = True
                injection = await middleware.maybe_inject("naive")
            response = await call_llm(prompt=task["task"], system_prompt="naive-baseline: answer the task directly.",
                                      model=get_settings().primary_model, max_tokens=1500)
            output = response.text if injection != "CORRUPT" else response.text[: len(response.text) // 2]
        except LLMError as exc:
            error = type(exc).__name__
    return {"answered": bool(output.strip()), "good": good_output(output), "status": "completed" if output else "failed",
            "outcome": "complete" if output else "failed", "steps": 0, "steps_success": 0, "llm_calls": calls,
            "retries": 0, "reflections": 0, "error": error, "answer": output}


async def run_graph(task: dict, task_id: str) -> dict:
    from agent.graph import run_agent
    from agent.state import final_results_by_step

    final = await run_agent(task_id, task["task"])
    results = final_results_by_step(final)
    steps = final.get("steps") or []
    steps_success = sum(1 for r in results.values() if r.status == "success")
    good = bool(steps) and len(results) == len(steps) and all(
        r.status == "success" and good_output(r.output) for r in results.values())
    summary = (final.get("final_output") or {}).get("summary") or {}
    return {"answered": final.get("status") == "completed" and steps_success > 0,
            "good": good, "status": final.get("status"), "outcome": summary.get("outcome"), "steps": len(steps),
            "steps_success": steps_success, "llm_calls": final.get("llm_calls") or [],
            "retries": sum((final.get("retry_counts") or {}).values()),
            "reflections": sum((final.get("reflection_counts") or {}).values()), "error": None,
            "answer": (final.get("final_output") or {}).get("result", "")}


async def run_one(task: dict, system: str, profile: str, seed: int, live: bool) -> dict:
    import random

    from agent.reliability import chaos, retry
    from eval.simulator import SimulatedProvider
    from services import llm_service

    configure(system, profile, live)
    # crc32, not hash(): str hashes are randomised per process, which would break reproducibility.
    rng_seed = zlib.crc32(f"{task['id']}|{system}|{profile}|{seed}".encode())
    random.seed(rng_seed)  # the chaos middleware uses the module-level RNG
    originals = (llm_service._call_openai, retry.compute_backoff_seconds, chaos.asyncio)
    if not live:
        provider = SimulatedProvider(rng_seed, profile, task["category"], primary_model="sim-primary")
        llm_service._call_openai = provider.call_openai
        retry.compute_backoff_seconds = lambda *a, **k: 0.0

        async def _no_sleep(_seconds):
            return None

        chaos.asyncio = SimpleNamespace(sleep=_no_sleep)  # chaos "latency" injections do not wait in simulation
    try:
        return await _run_and_record(task, system, profile, seed, live)
    finally:
        llm_service._call_openai, retry.compute_backoff_seconds, chaos.asyncio = originals


async def _run_and_record(task: dict, system: str, profile: str, seed: int, live: bool) -> dict:
    from services.llm_metrics import summarize_llm_calls

    task_id = f"{task['id']}-{system}-{profile}-{seed}"
    started = time.perf_counter()
    outcome = await (run_naive(task, task_id) if system == "naive" else run_graph(task, task_id))
    usage = summarize_llm_calls(outcome.pop("llm_calls"))
    answer = outcome.pop("answer")
    record = {"task_id": task["id"], "category": task["category"], "system": system, "profile": profile,
              "seed": seed, "mode": "live" if live else "simulated", **outcome,
              "calls": usage["calls"], "failed_calls": usage["failed_calls"], "tokens": usage["total_tokens"],
              "fallbacks": usage["fallbacks"], "provider_retries": usage["provider_retries"],
              "wall_ms": int((time.perf_counter() - started) * 1000)}
    if live:
        record["answer"] = answer[:4000]
        record["rubric"] = task["rubric"]
    return record


async def main_async(args) -> Path:
    tasks = load_tasks(args.limit)
    systems = args.systems or (("naive", "full") if args.live else SYSTEMS)
    profiles = args.profiles or (("clean",) if args.live else PROFILES)
    label = args.label or ("live" if args.live else "simulated")
    out = HERE / "results" / f"{label}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(tasks) * len(systems) * len(profiles) * len(args.seeds)
    done = 0
    with out.open("w") as handle:
        for seed in args.seeds:
            for profile in profiles:
                for system in systems:
                    for task in tasks:
                        record = await run_one(task, system, profile, seed, args.live)
                        handle.write(json.dumps(record) + "\n")
                        done += 1
                        if done % 40 == 0 or done == total:
                            print(f"{done}/{total} runs", file=sys.stderr)
    from eval.report import write_report

    write_report(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real provider configured in .env")
    parser.add_argument("--systems", nargs="*", choices=SYSTEMS)
    parser.add_argument("--profiles", nargs="*", choices=PROFILES)
    parser.add_argument("--seeds", nargs="*", type=int, default=[1])
    parser.add_argument("--limit", type=int, default=None, help="only the first N tasks")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()
    if not args.live:
        os.environ.update(SIM_ENV)  # before app imports: no real keys, no network, no Redis
    sys.path.insert(0, str(BACKEND))
    path = asyncio.run(main_async(args))
    print(f"wrote {path} and {path.with_suffix('.md')}")


if __name__ == "__main__":
    main()
