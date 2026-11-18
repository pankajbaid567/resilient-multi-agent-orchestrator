# Evaluation

Two questions, answered separately:

1. **Does the orchestration recover from failures?** (`python -m eval.run`, simulated provider)
2. **How well does it answer with a real model?** (`python -m eval.run --live`, needs a model key)

Only (1) has been run for this repository. Its results are in [`results/simulated.md`](results/simulated.md), generated from the raw per-run records in `results/simulated.jsonl`.

## 1. Orchestration under injected failures (simulated)

```bash
cd backend
python -m eval.run --seeds 1 2 3 --label simulated   # 1,080 runs, ~15 s on a laptop, no network
```

* **Tasks**: 40 tasks in `tasks.jsonl` across five categories (comparison, explanation, planning, analysis, writing). Each has a rubric used only in live mode.
* **Systems**: `naive` (one model call), `retry_only` (graph with validation and retries, one model, no circuit breaker, no reflection), `full` (retries, model fallback, circuit breaker, reflection).
* **Profiles**: `clean` (20% of worker answers low quality), `chaos` (the app's own chaos middleware: latency, empty responses, rate limits, cut-off responses, plus the 20%), `outage` (the primary model returns 503 on every call, plus the 20%).
* **Seeds** make every run reproducible: the same seed gives identical records (checked by `tests/unit/test_eval.py`).

### What it does not show

* The simulator (`simulator.py`) decides failure rates and its judge is perfect. Absolute numbers are properties of the simulator. Compare systems with each other, not with real-world reliability.
* Rewriting a step after reflection does **not** lower the simulated failure rate, so reflection only gains from the extra attempt it buys.
* Answer quality, latency and cost with real models are not measured here. Token counts are length/4 estimates.
* Chaos injections hit model calls made through the fallback layer, which since this phase means every role (planner, supervisor, workers, judge, reflector, finalizer).

## 2. Live runs (not yet run)

```bash
cd backend
# configure a model endpoint in .env first, for example a free Gemini or Groq key
python -m eval.run --live --limit 10 --label live
```

Runs `naive` and `full` on real models with the `clean` profile and records answers, rubric items, calls, real token counts and wall time. Scoring answers against `rubric` is a manual (or separately judged) step; until that is done, no quality claim should be made from live runs.
