# Mutation check (2026-11-18)

14 of 14 injected bugs caught by the unit tests (`python scripts/mutation_check.py`).

| mutant | file | result |
|---|---|---|
| 4xx client errors mapped as transient (retried) | `services/llm_service.py` | caught |
| backoff loses its cap | `agent/reliability/retry.py` | caught |
| circuit never opens | `agent/reliability/circuit_breaker.py` | caught |
| half-open admits unlimited probes | `agent/reliability/circuit_breaker.py` | caught |
| scheduler ignores dependencies | `agent/graph.py` | caught |
| token budget never enforced | `agent/graph.py` | caught |
| retries are unbounded | `agent/step_graph.py` | caught |
| cut-off answers are not retried | `agent/step_graph.py` | caught |
| private and metadata addresses allowed | `agent/tools/url_guard.py` | caught |
| untrusted fence can be closed early | `agent/tools/dispatch.py` | caught |
| step upsert keeps stale steps | `agent/state.py` | caught |
| fallbacks counted as retries | `services/llm_metrics.py` | caught |
| model-invented observations accepted | `agent/multi_agent/worker.py` | caught |
| judge's self-reported verdict trusted over scores | `agent/nodes/validator.py` | caught |

## History

* First run (2026-11-18): 12 of 14. Two survivors:
  * "4xx becomes retryable" was an **equivalent** mutant twice over: client errors are classified in both
    the retryable and non-retryable lists, so editing one list changes nothing. The mutant now changes the
    error mapping itself, which is where the behaviour lives.
  * "step upsert keeps stale steps" exposed a real gap: no test covered the `merge_steps` reducer. A reducer
    test was added (`test_state_reducers_upsert_steps_and_keep_first_abort_reason`).
* Second run: 14 of 14.
