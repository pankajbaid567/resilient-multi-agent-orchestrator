"""Single end-to-end integration test for the Reliable AI Agent system.

Run with:
    python tests/test_integration.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sys
import time
from typing import Any
from uuid import UUID

try:
    import requests
except ModuleNotFoundError:
    print("FAIL: requests is not installed. Install with: pip install requests")
    sys.exit(1)


BASE_URL = os.getenv("INTEGRATION_BASE_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 300


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _parse_iso8601(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _now() -> float:
    return time.perf_counter()


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_seconds: float
    details: str = ""


class IntegrationRunner:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.results: list[TestResult] = []

        self.task_id: str | None = None
        self.steps: list[dict[str, Any]] = []
        self.final_state: dict[str, Any] = {}
        self.primary_trace: list[dict[str, Any]] = []

    def run(self) -> int:
        overall_start = _now()

        print(f"Running end-to-end integration test against: {BASE_URL}")
        print("=" * 72)

        self._run_test("1) Health Check", self.test_health_check)
        self._run_test("2) Task Creation", self.test_task_creation)
        self._run_test("3) Task Execution", self.test_task_execution)
        self._run_test("4) Execution Trace", self.test_execution_trace)
        self._run_test("5) Metrics Accuracy", self.test_metrics_accuracy)
        self._run_test("6) Chaos Mode", self.test_chaos_mode)
        self._run_test("7) Error Handling", self.test_error_handling)

        overall_duration = _now() - overall_start
        passed_count = sum(1 for result in self.results if result.passed)
        failed_count = len(self.results) - passed_count

        print("\n" + "=" * 72)
        print("SUMMARY")
        print("=" * 72)
        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            detail_suffix = f" | {result.details}" if result.details else ""
            print(f"{status:4} | {result.name:<24} | {result.duration_seconds:7.2f}s{detail_suffix}")

        print("-" * 72)
        print(
            "Total: "
            f"{len(self.results)} tests, "
            f"Passed: {passed_count}, "
            f"Failed: {failed_count}, "
            f"Duration: {overall_duration:.2f}s"
        )

        return 0 if failed_count == 0 else 1

    def _run_test(self, name: str, test_fn) -> None:
        start = _now()
        try:
            test_fn()
            duration = _now() - start
            self.results.append(TestResult(name=name, passed=True, duration_seconds=duration))
            print(f"PASS | {name} ({duration:.2f}s)")
        except Exception as exc:
            duration = _now() - start
            self.results.append(
                TestResult(name=name, passed=False, duration_seconds=duration, details=str(exc))
            )
            print(f"FAIL | {name} ({duration:.2f}s) -> {exc}")

    def _get_json(self, response: requests.Response) -> dict[str, Any]:
        try:
            return response.json()
        except Exception as exc:
            raise AssertionError(f"Response was not valid JSON. status={response.status_code}") from exc

    def _create_task(self, task_text: str) -> tuple[str, list[dict[str, Any]]]:
        response = self.session.post(
            f"{BASE_URL}/tasks",
            json={"task": task_text},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 200, f"Expected 200 creating task, got {response.status_code}")

        payload = self._get_json(response)
        _assert(payload.get("success") is True, f"Task creation success flag false: {payload}")
        data = payload.get("data") or {}

        task_id = data.get("task_id")
        steps = data.get("steps")
        _assert(isinstance(task_id, str) and task_id, "Task creation did not return task_id")
        _assert(isinstance(steps, list), "Task creation did not return steps list")

        return task_id, steps

    def _execute_task(self, task_id: str) -> None:
        response = self.session.post(
            f"{BASE_URL}/tasks/{task_id}/execute",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 200, f"Expected 200 on execute, got {response.status_code}")
        payload = self._get_json(response)
        _assert(payload.get("success") is True, f"Execute response did not indicate success: {payload}")

    def _wait_for_completion(self, task_id: str) -> dict[str, Any]:
        started = _now()
        last_state: dict[str, Any] = {}

        while (_now() - started) <= MAX_WAIT_SECONDS:
            response = self.session.get(
                f"{BASE_URL}/tasks/{task_id}",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            _assert(response.status_code == 200, f"Expected 200 polling task, got {response.status_code}")
            payload = self._get_json(response)
            _assert(payload.get("success") is True, f"Task polling success flag false: {payload}")

            state = payload.get("data")
            _assert(isinstance(state, dict), "Task polling did not return state object")
            last_state = state

            status = str(state.get("status", "")).lower()
            if status in {"completed", "failed"}:
                return state

            time.sleep(POLL_INTERVAL_SECONDS)

        raise AssertionError(
            f"Timed out after {MAX_WAIT_SECONDS}s waiting for completion. "
            f"Last status={last_state.get('status')}"
        )

    def _get_trace(self, task_id: str) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{BASE_URL}/traces/{task_id}",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 200, f"Expected 200 getting trace, got {response.status_code}")
        payload = self._get_json(response)
        _assert(payload.get("success") is True, f"Trace response success flag false: {payload}")

        data = payload.get("data") or {}
        trace = data.get("trace")
        _assert(isinstance(trace, list), "Trace payload does not contain list under data.trace")
        return trace

    def _set_chaos_mode(self, enabled: bool) -> dict[str, Any]:
        response = self.session.post(
            f"{BASE_URL}/config/chaos",
            json={"enabled": enabled},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 200, f"Expected 200 toggling chaos mode, got {response.status_code}")
        payload = self._get_json(response)
        _assert(payload.get("chaos_mode") is enabled, f"Chaos mode not set to {enabled}: {payload}")
        return payload

    # --- Tests ---

    def test_health_check(self) -> None:
        response = self.session.get(f"{BASE_URL}/health", timeout=REQUEST_TIMEOUT_SECONDS)
        _assert(response.status_code == 200, f"Expected HTTP 200, got {response.status_code}")

        payload = self._get_json(response)
        _assert(payload.get("status") == "ok", f"Expected status=ok, got {payload.get('status')}")
        _assert(
            payload.get("redis") == "connected",
            f"Expected redis=connected, got {payload.get('redis')}",
        )

    def test_task_creation(self) -> None:
        task_id, steps = self._create_task("List 3 benefits of regular exercise")

        try:
            UUID(task_id)
        except ValueError as exc:
            raise AssertionError(f"task_id is not a valid UUID: {task_id}") from exc

        _assert(2 <= len(steps) <= 10, f"Expected 2-10 steps, got {len(steps)}")

        required_fields = {"step_id", "name", "description", "tool_needed"}
        for index, step in enumerate(steps, start=1):
            _assert(isinstance(step, dict), f"Step {index} is not an object")
            missing = [field for field in required_fields if field not in step]
            _assert(not missing, f"Step {index} missing fields: {missing}")

        self.task_id = task_id
        self.steps = steps

    def test_task_execution(self) -> None:
        _assert(self.task_id is not None, "Task execution requires task_id from Task Creation test")

        self._execute_task(self.task_id)
        state = self._wait_for_completion(self.task_id)

        status = str(state.get("status", "")).lower()
        _assert(status == "completed", f"Expected final status completed, got {state.get('status')}")

        step_results = state.get("step_results")
        _assert(isinstance(step_results, list), "step_results is missing or not a list")
        _assert(
            len(step_results) >= len(self.steps),
            f"Expected at least {len(self.steps)} step_results, got {len(step_results)}",
        )

        _assert(state.get("final_output") is not None, "final_output must not be None")
        _assert(state.get("confidence_score") is not None, "confidence_score must be set")

        self.final_state = state

    def test_execution_trace(self) -> None:
        _assert(self.task_id is not None, "Execution Trace test requires task_id")
        trace = self._get_trace(self.task_id)

        _assert(len(trace) > 0, "Trace is empty")
        event_types = [str(event.get("event_type", "")) for event in trace]

        _assert("task_started" in event_types, "Expected trace to contain task_started event")
        _assert(
            "step_completed" in event_types,
            "Expected trace to contain at least one step_completed event",
        )
        _assert("task_completed" in event_types, "Expected trace to contain task_completed event")

        parsed_times: list[datetime] = []
        for index, event in enumerate(trace, start=1):
            timestamp = event.get("timestamp")
            _assert(isinstance(timestamp, str), f"Trace event {index} missing timestamp")
            try:
                parsed_times.append(_parse_iso8601(timestamp))
            except Exception as exc:
                raise AssertionError(f"Invalid timestamp on trace event {index}: {timestamp}") from exc

        for index in range(1, len(parsed_times)):
            previous = parsed_times[index - 1]
            current = parsed_times[index]
            _assert(
                current >= previous,
                "Trace events are not in chronological order",
            )

        self.primary_trace = trace

    def test_metrics_accuracy(self) -> None:
        _assert(self.final_state, "Metrics Accuracy test requires final_state from Task Execution test")

        llm_tokens_used = self.final_state.get("llm_tokens_used")
        _assert(isinstance(llm_tokens_used, int), f"llm_tokens_used must be int, got {type(llm_tokens_used)}")
        _assert(llm_tokens_used > 0, f"Expected llm_tokens_used > 0, got {llm_tokens_used}")

        started_at = self.final_state.get("started_at")
        completed_at = self.final_state.get("completed_at")
        _assert(isinstance(started_at, str) and started_at, "started_at must be set")
        _assert(isinstance(completed_at, str) and completed_at, "completed_at must be set")

        started_dt = _parse_iso8601(started_at)
        completed_dt = _parse_iso8601(completed_at)
        _assert(completed_dt > started_dt, "completed_at must be greater than started_at")

        step_results = self.final_state.get("step_results")
        _assert(isinstance(step_results, list), "step_results must be a list")
        _assert(
            len(step_results) == len(self.steps),
            f"Expected len(step_results) == len(steps), got {len(step_results)} vs {len(self.steps)}",
        )

    def test_chaos_mode(self) -> None:
        chaos_enabled = False
        try:
            self._set_chaos_mode(True)
            chaos_enabled = True

            task_id, _ = self._create_task("Explain photosynthesis")
            self._execute_task(task_id)
            final_state = self._wait_for_completion(task_id)

            _assert(
                str(final_state.get("status", "")).lower() == "completed",
                f"Chaos-mode task expected completed, got {final_state.get('status')}",
            )

            trace = self._get_trace(task_id)
            event_types = [str(event.get("event_type", "")) for event in trace]
            reliability_events = {
                event_type
                for event_type in event_types
                if event_type in {"retry_triggered", "fallback_triggered"}
            }
            _assert(
                len(reliability_events) >= 1,
                "Expected at least one reliability event in chaos mode (retry_triggered or fallback_triggered)",
            )
        finally:
            if chaos_enabled:
                self._set_chaos_mode(False)

    def test_error_handling(self) -> None:
        # Empty body should fail schema validation.
        response = self.session.post(
            f"{BASE_URL}/tasks",
            json={},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 422, f"Expected 422 for empty task payload, got {response.status_code}")

        # Unknown task id should return 404.
        response = self.session.get(
            f"{BASE_URL}/tasks/nonexistent-id",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 404, f"Expected 404 for unknown task, got {response.status_code}")

        # Unknown trace id should return 404.
        response = self.session.get(
            f"{BASE_URL}/traces/nonexistent-id",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _assert(response.status_code == 404, f"Expected 404 for unknown trace, got {response.status_code}")


def main() -> int:
    runner = IntegrationRunner()
    try:
        return runner.run()
    finally:
        runner.session.close()


if __name__ == "__main__":
    raise SystemExit(main())