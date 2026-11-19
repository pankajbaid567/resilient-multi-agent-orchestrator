# Demo script (5 minutes)

Goal: show that the system plans, runs agents in parallel, checks its own work, and keeps answering
when models fail, and be honest about where that machinery pays off.

## Before you start

- Live UI: https://reliable-agent.pages.dev (API: https://3-109-36-90.sslip.io/docs).
- The public demo allows 5 runs per visitor per hour. Do one warm-up run, not five.
- The public demo has no web search and no code execution, so use tasks that do not need live data
  (see [scenarios.json](scenarios.json)).
- Chaos mode (fault injection) is admin-only on the public demo. To show recovery live, run locally:
  `CHAOS_MODE=true uvicorn main:app` in `backend/`, or show the simulated eval table in the README.

## Run of show

| Time | Say | Do | Point at |
|---|---|---|---|
| 0:00–0:30 | "A task runner on LangGraph: plan, run specialist agents, review every answer, recover from model failures. API on EC2, models on Amazon Bedrock through the instance's IAM role, no stored keys." | Open the UI, open *About this demo*. | Architecture in two sentences. |
| 0:30–1:30 | "First it only plans. Nothing runs until I approve it." | Paste scenario 1, keep *Show me the plan before it runs* ticked, click *Plan task*. | Steps, waves (what runs in parallel), dependencies. |
| 1:30–2:30 | "Steps in the same wave run at the same time. Each answer is checked by a reviewer model." | Click *Run plan*. | Live graph: agent and model per step; live log. |
| 2:30–3:15 | "The answer, with what it cost." | Wait for the answer card. | Outcome, confidence, time, model calls, tokens, cost (about $0.003). |
| 3:15–4:00 | "Every number comes from recorded model calls." | Open *Metrics*, then *Trace*. | Calls by role (planner, agents, reviewer, finalizer), latency p50/p95, per-step table; event waterfall. |
| 4:00–4:40 | "Where does it pay off? Not on quality: on 40 tasks it ties a single call and costs about 3x. Under provider failures it keeps answering." | Show the README eval tables (simulated and live). | Outage: single call 0% answered, full system 100%. |
| 4:40–5:00 | "Built for failure: retries, fallback models, circuit breakers, checkpoints, budgets, and an honest eval." | Back to the Run tab. | Close. |

## If something goes wrong

- **429 Too Many Requests:** the per-visitor limit (5/hour) is hit. Show a run from the local stack instead.
- **Slow plan:** narrate the plan-approval pause while it loads; planning is one model call.
- **A step fails review:** good, show it. The step gets retried with the reviewer's feedback, or the
  reflector rewrites, splits or skips it; the answer card then says *partial*.

## Likely questions

| Question | Answer |
|---|---|
| How is this different from one prompt? | It plans, runs steps with the right agent, reviews each answer and recovers from failures. On quality alone it ties one call with a strong model; the gain is reliability under failures, at about 3x the cost. |
| What happens when a model is down? | Retries with backoff, then the next model in the chain; a circuit breaker stops calling a failing model. Every role goes through this, including the planner and the reviewer. |
| What if the process dies mid-run? | State is checkpointed after every wave; `POST /tasks/{id}/resume` re-runs only the unfinished steps. |
| How do you know the tests test anything? | 101 tests with a scripted fake model, and a mutation check: 14 injected bugs, 14 caught. |
| How do you stop runaway cost? | A token budget and a deadline per task, rate limits per visitor and per day, and an AWS Budget alert. |
