# Evaluation

Two questions, answered separately:

1. **Does the orchestration recover from failures?** (`python -m eval.run`, simulated provider)
2. **How well does it answer with a real model?** (`python -m eval.run --live`, needs a model key)

Both have been run. (1): [`results/simulated.md`](results/simulated.md), generated from the raw per-run records in `results/simulated.jsonl`. (2): on Amazon Bedrock, two versions, [`results/live-v1.graded.md`](results/live-v1.graded.md) and [`results/live-v2.graded.md`](results/live-v2.graded.md), with raw records next to them. The project README summarises both runs and what changed between them.

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

## 2. Live runs (Amazon Bedrock)

```bash
cd backend
pip install -r requirements-bedrock.txt
aws login --profile <profile>                       # credentials for the short-term Bedrock key
python -m eval.run --live --bedrock --profile <profile> --start 0 --limit 10 --label live-part1
python -m eval.grade eval/results/live.jsonl --bedrock --profile <profile>
```

* The run uses in-Region, on-demand models in `ap-south-1` (`eval/bedrock.py`). Prices come from the AWS Pricing API, so each record carries a real cost.
* `--start/--limit` run the tasks in chunks, so a long run can be split up.
* `eval/grade.py` scores each answer against the task's rubric using a different model from the one that wrote it. This is AI-graded. Re-grading the same answers drifted by 0.8 points on average.
* v2 was changed after seeing v1 on the same 40 tasks. It is not a held-out result.
