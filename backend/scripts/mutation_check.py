"""Inject one bug at a time into core logic and check the unit tests catch it.

    cd backend && python scripts/mutation_check.py

Each mutant replaces one exact snippet; the suite must fail for the mutant to count as
caught. Files are restored after every mutant (and on Ctrl-C). Do not edit source files
while this runs. Results go to docs/mutation-results.md.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

MUTANTS = [
    # Two earlier versions of this mutant (removing LLMRequestError from NON_RETRYABLE, or adding it to
    # RETRYABLE) were equivalent: the error is classified in both lists. The behaviour lives in the mapping.
    ("services/llm_service.py", "        return LLMRequestError(f\"LLM request rejected", "        return LLMServerError(f\"LLM request rejected",
     "4xx client errors mapped as transient (retried)"),
    ("agent/reliability/retry.py", "return min(base_seconds * (2**attempt) + jitter, max_seconds)",
     "return base_seconds * (2**attempt) + jitter", "backoff loses its cap"),
    ("agent/reliability/circuit_breaker.py", "failure_rate > self.failure_threshold", "failure_rate > 1.0",
     "circuit never opens"),
    ("agent/reliability/circuit_breaker.py", "                if self._half_open_probe_in_flight:\n                    return True",
     "                if False:\n                    return True", "half-open admits unlimited probes"),
    ("agent/graph.py", "ready = [step for step in pending if all(dep in finished for dep in step.dependencies)]",
     "ready = pending", "scheduler ignores dependencies"),
    ("agent/graph.py", "if token_budget > 0 and _safe_int(state.get(\"llm_tokens_used\")) >= token_budget:",
     "if False:", "token budget never enforced"),
    ("agent/step_graph.py", "if state.get(\"verdict\") == \"retry\" and state.get(\"attempt\", 0) < max_retries:",
     "if state.get(\"verdict\") == \"retry\":", "retries are unbounded"),
    ("agent/step_graph.py", "        elif state.get(\"last_truncated\"):", "        elif False:",
     "cut-off answers are not retried"),
    ("agent/tools/url_guard.py", "if not address.is_global or address.is_multicast:", "if address.is_multicast:",
     "private and metadata addresses allowed"),
    ("agent/tools/dispatch.py", ".replace(\"</tool_output>\", \"</ tool_output>\")", "",
     "untrusted fence can be closed early"),
    ("agent/state.py", "        by_id[step.step_id] = step", "        by_id.setdefault(step.step_id, step)",
     "step upsert keeps stale steps"),
    ("services/llm_metrics.py", "            if last_failed[key] == model:\n                retries += 1",
     "            if True:\n                retries += 1", "fallbacks counted as retries"),
    ("agent/multi_agent/worker.py", "reply = text.split(\"\\nObservation:\", 1)[0].strip()", "reply = text.strip()",
     "model-invented observations accepted"),
    ("agent/nodes/validator.py", "    elif verdict != derived_verdict:\n        verdict = derived_verdict",
     "    elif False:\n        verdict = derived_verdict", "judge's self-reported verdict trusted over scores"),
]


HISTORY = """
## History

* First run (2026-11-18): 12 of 14. Two survivors:
  * "4xx becomes retryable" was an **equivalent** mutant twice over: client errors are classified in both
    the retryable and non-retryable lists, so editing one list changes nothing. The mutant now changes the
    error mapping itself, which is where the behaviour lives.
  * "step upsert keeps stale steps" exposed a real gap: no test covered the `merge_steps` reducer. A reducer
    test was added (`test_state_reducers_upsert_steps_and_keep_first_abort_reason`).
* Second run: 14 of 14.
"""


def run_tests() -> bool:
    result = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"],
                            cwd=BACKEND, capture_output=True, text=True)
    return result.returncode == 0


def main() -> int:
    if not run_tests():
        print("baseline suite fails; fix it before running mutants")
        return 1
    rows, caught = [], 0
    for path, old, new, label in MUTANTS:
        file = BACKEND / path
        original = file.read_text()
        if original.count(old) != 1:
            print(f"SKIP (snippet not found exactly once): {label}")
            rows.append((label, path, "snippet missing"))
            continue
        try:
            file.write_text(original.replace(old, new))
            survived = run_tests()
        finally:
            file.write_text(original)
        caught += not survived
        status = "survived" if survived else "caught"
        print(f"{status:9s} {label}")
        rows.append((label, path, status))
    total = len(MUTANTS)
    lines = [f"# Mutation check ({date.today().isoformat()})", "",
             f"{caught} of {total} injected bugs caught by the unit tests (`python scripts/mutation_check.py`).", "",
             "| mutant | file | result |", "|---|---|---|"]
    lines += [f"| {label} | `{path}` | {status} |" for label, path, status in rows]
    out = BACKEND.parent / "docs" / "mutation-results.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n" + HISTORY)
    print(f"{caught}/{total} caught -> {out}")
    return 0 if caught == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
