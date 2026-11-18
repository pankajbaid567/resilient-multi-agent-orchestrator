# Reliable AI Agent

A multi-step, multi-agent task runner built on **LangGraph** that keeps working when models fail. A planner splits a task into a dependency graph of steps. A supervisor hands each step to a specialist agent (research, code, analysis, writing) that runs a ReAct loop with its own tools. Every answer is validated, and failing steps are retried with the reviewer's feedback or repaired by a reflector. Model calls go through retries, a fallback chain of models and per-model circuit breakers. Run state is checkpointed after every wave of steps, so an interrupted run resumes where it stopped.

**What is measured, and how:**
- **91 deterministic unit tests** run against a scripted fake model: no network, no Redis, no keys.
- **A mutation check** injects 14 bugs into the core logic, and the tests catch 14 of 14 ([details and history](docs/mutation-results.md)).
- **A reproducible eval** runs 1,080 times on a *simulated* model with injected faults ([report](backend/eval/results/simulated.md)). It measures recovery, not answer quality.
- **Real-model answer quality has not been measured yet** (see [Limits](#limits)).

## How it works

```mermaid
graph TD
    start([start]) --> planner["planner<br/><small>pauses here for plan approval</small>"]
    planner --> scheduler
    scheduler -. "Send, one per ready step" .-> step_worker["step_worker<br/>(step subgraph)"]
    step_worker --> scheduler
    scheduler -. "budget or deadline hit" .-> budget_stop
    scheduler -. "all steps done or aborted" .-> finalizer
    budget_stop --> finalizer
    finalizer --> done([end])
```

Each `step_worker` runs this subgraph:

```mermaid
graph TD
    start([start]) --> execute["execute<br/><small>supervisor picks the agent and writes a brief,<br/>then the agent runs its ReAct loop</small>"]
    execute --> validate["validate<br/><small>deterministic checks, then an LLM judge</small>"]
    validate -. pass .-> finish
    validate -. "retry, under MAX_RETRIES" .-> retry["retry<br/><small>adds the judge's feedback</small>"]
    retry --> execute
    validate -. "retries used up, or the verdict is reflect" .-> reflect["reflect<br/><small>modify / decompose / skip / abort</small>"]
    reflect -. modify .-> execute
    reflect -. "decompose, skip or abort" .-> finish
    finish --> done([end])
```

Both diagrams match the compiled graphs (`build_graph(...).get_graph().draw_mermaid()`).

| Concern | How it is handled | Where |
|---|---|---|
| Parallel steps | The scheduler sends every step whose dependencies have finished, through LangGraph `Send` (one per wave when `PARALLEL_MODE=false`). Reducers merge the logs of concurrent steps. | `agent/graph.py`, `agent/state.py` |
| Resume | The LangGraph checkpointer saves state after every super-step (thread = task id). A crashed run re-runs only the steps that had not finished. Backends: memory, SQLite, or Postgres (for example free Neon). | `agent/checkpointing.py` |
| Human in the loop | The run pauses after planning. `POST /tasks` returns the plan, and `POST /tasks/{id}/execute` approves it and continues. | `interrupt_after=["planner"]` |
| Multi-agent | The supervisor model picks the specialist and writes its brief, with deterministic rules as fallback. Workers run a text-protocol ReAct loop with only the tools their role allows and the deployment can run. Observations invented by the model are discarded. | `agent/multi_agent/` |
| Validation | Checks run in order: empty output, too short, cut off by the token limit (`finish_reason=length`), then the LLM judge. The verdict comes from the judge's scores, not from the verdict it states. | `agent/nodes/validator.py`, `agent/step_graph.py` |
| Provider failures | Errors are classified: 4xx fails fast, while 5xx, 429, timeouts and empty bodies are retried with backoff and jitter. Then the next model in `MODEL_CHAIN` is tried. Every role goes through this layer: planner, supervisor, workers, judge, reflector, finalizer. | `agent/reliability/` |
| Runaway cost | Each task has a token budget and a deadline. When either is hit, the run stops and reports partial results. | `TASK_TOKEN_BUDGET`, `TASK_DEADLINE_SECONDS` |
| Prompt injection via tools | Tool output is fenced as untrusted data. A failed tool is reported to the model as missing data ("do not invent"). | `agent/tools/dispatch.py` |

## Observability

- **Every model call is recorded**: node, step, model, input/output tokens as reported by the provider, latency and success. The final summary and `GET /metrics/{task_id}` derive these numbers from those records:
  - tokens in/out
  - latency p50/p95
  - output tokens per second
  - failed calls
  - fallbacks and same-model retries
  - a breakdown by node and by model
- **Cost** is reported only when every model used has a price in `MODEL_PRICES`. Otherwise it is left empty instead of being estimated.
- **OpenTelemetry spans** cover task, graph node, step, agent, model call (GenAI conventions such as `gen_ai.usage.input_tokens`) and tool call. They are exported over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, for example to a local Jaeger or a hosted OTLP endpoint.
- **Live events** go to a per-task Redis Stream, so a browser that connects late or reconnects replays what it missed. Without Redis, an in-memory log behaves the same way.

## Eval (simulated provider)

`cd backend && python -m eval.run --seeds 1 2 3` runs 40 tasks × 3 systems × 3 fault profiles × 3 seeds. It takes about 15 s on a laptop and needs no network. The same seed gives the same records.

| Profile | naive (one call) | retry_only (graph, one model, no breaker, no reflection) | full |
|---|---|---|---|
| clean (20% of answers low quality) | 75.8% good | 95.8% good | 100.0% good |
| chaos (app's chaos middleware + the 20%) | 36.7% good | 58.3% good | 98.3% good |
| outage (primary model always 503) | 0% answered | 0% answered | 100.0% good |

"Good" means every delivered part passes the simulator's ground truth. The intervals, call counts and token counts are in the [report](backend/eval/results/simulated.md), and every run's raw record is in `simulated.jsonl`. **These are properties of the simulator**: it picks the failure rates, its judge is perfect, and reflection gets no built-in advantage beyond an extra attempt. The numbers show that the mechanisms work as designed and how the three systems compare. They say nothing about real-world reliability or answer quality. The full system spends about 13–23 model calls per task where naive spends one.

## Run it

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set a model endpoint, see below
uvicorn main:app --reload     # http://localhost:8000/docs

# checks
ruff check . && python -m pytest -q
python -m eval.run --limit 5
python scripts/mutation_check.py

# frontend
cd ../frontend && npm ci && npm run dev   # http://localhost:5173
```

**Models.** Any OpenAI-compatible endpoint works. Set `OPEN_SOURCE_BASE_URL`, `OPEN_SOURCE_API_KEY` and `MODEL_CHAIN` (comma-separated, tried in order). The Hugging Face router, the Gemini API's OpenAI-compatible endpoint, Groq and a local Ollama all fit. Free tiers change, so check the provider. If an endpoint rejects explicit temperatures, set `LLM_SEND_TEMPERATURE=false`.

**Optional:**
- `TAVILY_API_KEY` enables web search for the research agent.
- `CHECKPOINT_URL=postgresql://...` makes resume survive restarts.
- `docker compose up --build` runs Redis, the backend and the frontend together.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/tasks` | Plan a task. The run pauses and the response has the steps |
| `POST` | `/tasks/{id}/execute` | Approve the plan and run (`already_running` if a run is active) |
| `POST` | `/tasks/{id}/resume` | Continue an interrupted run from its last checkpoint |
| `GET` | `/tasks/{id}` | Full saved state |
| `GET` | `/traces/{id}`, `/traces/{id}/summary` | Trace events |
| `GET` | `/metrics/{id}`, `/metrics`, `/metrics/providers` | Task LLM usage, aggregates, circuit states |
| `GET` | `/config` | Runtime settings and the active model chain |
| `POST` | `/config*` | Change settings. Needs `X-Admin-Key`, and is refused when `ADMIN_API_KEY` is unset |
| `WS` | `/ws/{id}` | Live events, replayed from the start |

## Security defaults

Two tools act on the server's behalf, so both are **off unless an operator enables them**:

| Switch | Default | What it allows | Guard when enabled |
|---|---|---|---|
| `ENABLE_CODE_EXEC` | `false` | The code agent runs Python | The child process gets an empty environment (no API keys), isolated mode, a temp dir, CPU/file/process limits and truncated output. **Network is not blocked: this is not a sandbox.** |
| `ENABLE_API_CALL` | `false` | The research agent fetches URLs | http(s) only, and every resolved address must be public (blocks loopback, private and cloud-metadata ranges). Redirects are not followed. DNS rebinding is not covered. |

Endpoints that change process-wide settings need `X-Admin-Key`, so a public deployment is read-only. To change settings from the UI, store the key in the browser with `localStorage.setItem("agent_admin_key", "...")`.

## Limits

- **No real-model eval yet.** The Hugging Face free credits this project used ran out (HTTP 402). `python -m eval.run --live` records answers, rubric items, real token counts and time with any configured endpoint, but scoring those answers has not been done. Make no quality claim until it is.
- **Python execution is not sandboxed**: there is no network isolation. Keep `ENABLE_CODE_EXEC=false` anywhere untrusted users can submit tasks.
- **Resume granularity is one step.** A step that was running when the process died runs again from its start. Its model calls are not idempotent, so they are repeated (and paid for again).
- **The in-memory checkpointer** (the default) loses runs on restart. Use SQLite or Postgres to resume across restarts.
- **The circuit breaker state is per process.** Several workers or instances do not share it.
- The simulated eval's numbers depend on the simulator's design, as stated above.

## Project layout

```
backend/
  agent/graph.py            planner -> scheduler -> Send(step_worker) -> finalizer, runtime helpers
  agent/step_graph.py       per-step subgraph: execute -> validate -> retry | reflect -> finish
  agent/state.py            graph state and reducers
  agent/multi_agent/        supervisor, ReAct worker, agent definitions
  agent/nodes/              planner, judge, reflector, finalizer
  agent/reliability/        retry, fallback chain, circuit breaker, chaos injection
  agent/tools/              web search, HTTP GET, Python; URL guard, untrusted-output fencing
  services/                 LLM client, usage records, LLM metrics, Redis streams
  eval/                     tasks, simulator, runner, report, results
  scripts/mutation_check.py
  tests/unit/               91 tests with a scripted fake model
frontend/                   React + Vite dashboard
```

## License

MIT, see [LICENSE](LICENSE).
