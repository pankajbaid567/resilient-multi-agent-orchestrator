# Orchestration eval (simulated)

Runs: 1080 · tasks: 40 · seeds: [1, 2, 3] · generated from `simulated.jsonl` by `eval/report.py`.

**Read this first.** These runs use `eval/simulator.py`, not a real model. They measure whether the orchestration (retries, model fallback, reflection, scheduling) recovers from the failures the simulator injects. They say nothing about answer quality with real models. The simulated judge is perfect and the failure rates are chosen by the simulator, so absolute numbers are properties of the simulator; compare systems against each other, not against the outside world.

* **answered**: the run produced a final answer (naive: the single call succeeded).
* **good**: every delivered part is correct by the simulator's ground truth (no low-quality marker, not truncated by chaos corruption). Naive has no validation, so its bad answers are delivered as-is.
* Intervals are 95% bootstrap over tasks. Calls, tokens, fallbacks, retries and reflect visits are means per task (tokens are length/4 estimates). A reflect visit with reflection disabled is a skip.

| profile | system | n | answered | good | calls | tokens | fallbacks | retries | reflect visits |
|---|---|---|---|---|---|---|---|---|---|
| chaos | naive | 120 | 65.0% (55.8–73.3) | 36.7% (27.5–45.0) | 0.7 | 42 | 0.00 | 0.00 | 0.00 |
| chaos | retry_only | 120 | 99.2% (97.5–100.0) | 58.3% (50.0–67.5) | 20.7 | 6423 | 0.00 | 2.83 | 0.52 |
| chaos | full | 120 | 100.0% (100.0–100.0) | 98.3% (95.8–100.0) | 22.9 | 7041 | 1.92 | 1.58 | 0.23 |
| clean | naive | 120 | 100.0% (100.0–100.0) | 75.8% (68.3–83.3) | 1.0 | 65 | 0.00 | 0.00 | 0.00 |
| clean | retry_only | 120 | 100.0% (100.0–100.0) | 95.8% (91.7–99.2) | 12.8 | 5731 | 0.00 | 0.91 | 0.04 |
| clean | full | 120 | 100.0% (100.0–100.0) | 100.0% (100.0–100.0) | 12.7 | 5687 | 0.00 | 0.78 | 0.03 |
| outage | naive | 120 | 0.0% (0.0–0.0) | 0.0% (0.0–0.0) | 1.0 | 0 | 0.00 | 0.00 | 0.00 |
| outage | retry_only | 120 | 0.0% (0.0–0.0) | 0.0% (0.0–0.0) | 8.0 | 0 | 0.00 | 0.00 | 0.00 |
| outage | full | 120 | 100.0% (100.0–100.0) | 100.0% (100.0–100.0) | 19.3 | 5743 | 3.20 | 0.78 | 0.04 |
