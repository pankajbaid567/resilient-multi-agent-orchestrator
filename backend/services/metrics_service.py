"""Metrics computation service for task, trace, and provider observability."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent.state import AgentState
from models import AggregateMetrics, TaskMetrics, TraceSummary

MODEL_COST_RATES_USD_PER_MILLION = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3.5": {"input": 3.00, "output": 15.00},
}


class MetricsService:
    """Compute and cache task/aggregate metrics from AgentState payloads."""

    def __init__(self):
        self._task_metrics: Dict[str, TaskMetrics] = {}
        self._provider_metrics_by_task: Dict[str, Dict[str, dict[str, Any]]] = {}

    def compute_task_metrics(self, state: AgentState) -> TaskMetrics:
        """Compute all metrics from a completed AgentState."""
        state_data = dict(state or {})
        task_id = str(state_data.get("task_id") or "")
        status = str(state_data.get("status") or "unknown")

        steps = self._as_list(state_data.get("steps"))
        step_results = self._as_list(state_data.get("step_results"))
        trace = self._normalize_trace(state_data.get("execution_trace"))
        error_log = self._as_list(state_data.get("error_log"))

        total_steps = len(steps) if steps else len(step_results)
        successful_steps = sum(1 for result in step_results if self._result_status(result) == "success")
        failed_steps = sum(1 for result in step_results if self._result_status(result) == "failed")
        skipped_steps = sum(1 for result in step_results if self._result_status(result) == "skipped")

        step_durations = [
            max(0, self._to_int(self._get_field(result, "latency_ms")))
            for result in step_results
        ]
        total_duration_ms = self._compute_duration_ms(
            started_at=state_data.get("started_at"),
            completed_at=state_data.get("completed_at"),
        )
        if total_duration_ms <= 0 and step_durations:
            total_duration_ms = int(sum(step_durations))

        avg_step_duration_ms = float(sum(step_durations) / len(step_durations)) if step_durations else 0.0
        max_step_duration_ms = max(step_durations) if step_durations else 0

        retry_count = max(
            sum(self._to_int(value) for value in dict(state_data.get("retry_counts") or {}).values()),
            sum(1 for event in trace if str(event.get("event_type") or "") == "retry_triggered"),
        )
        fallback_count = sum(1 for event in trace if str(event.get("event_type") or "") == "fallback_triggered")
        reflection_count = max(
            sum(self._to_int(value) for value in dict(state_data.get("reflection_counts") or {}).values()),
            sum(
                1
                for event in trace
                if str(event.get("event_type") or "") in {"reflection_started", "reflection_completed"}
            ),
        )

        llm_tokens_used = max(0, self._to_int(state_data.get("llm_tokens_used")))
        tokens_input, tokens_output = self._compute_token_breakdown(
            trace=trace,
            step_results=step_results,
            llm_tokens_used=llm_tokens_used,
        )
        total_tokens = tokens_input + tokens_output

        models_used = self._collect_models_used(step_results=step_results, trace=trace)
        tools_used = self._collect_tools_used(step_results=step_results, trace=trace)
        agents_used = self._collect_agents_used(step_results=step_results, trace=trace)

        quality_scores = self._collect_quality_scores(error_log=error_log)
        failure_types = self._collect_failure_types(error_log=error_log, step_results=step_results)
        reflection_strategies = self._collect_reflection_strategies(trace=trace)

        estimated_cost_usd = self._estimate_cost_usd(
            step_results=step_results,
            trace=trace,
            fallback_total_tokens=total_tokens,
        )

        time_saved_parallel_ms = self._estimate_parallel_time_saved(
            state_data=state_data,
            trace=trace,
            total_step_runtime_ms=int(sum(step_durations)) if step_durations else 0,
            wall_clock_ms=total_duration_ms,
        )

        return TaskMetrics(
            task_id=task_id,
            status=status,
            total_steps=total_steps,
            successful_steps=successful_steps,
            failed_steps=failed_steps,
            skipped_steps=skipped_steps,
            total_tokens=total_tokens,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            estimated_cost_usd=estimated_cost_usd,
            total_duration_ms=total_duration_ms,
            avg_step_duration_ms=round(avg_step_duration_ms, 2),
            max_step_duration_ms=max_step_duration_ms,
            retry_count=retry_count,
            fallback_count=fallback_count,
            reflection_count=reflection_count,
            confidence_score=self._normalize_optional_string(state_data.get("confidence_score")),
            quality_scores=quality_scores,
            models_used=models_used,
            tools_used=tools_used,
            agents_used=agents_used,
            time_saved_parallel_ms=time_saved_parallel_ms,
            failure_types=failure_types,
            reflection_strategies=reflection_strategies,
        )

    def record_task_metrics(self, task_id: str, state: AgentState):
        """Compute and store metrics for a completed task."""
        metrics = self.compute_task_metrics(state)
        effective_task_id = str(task_id or metrics.task_id)
        if not effective_task_id:
            return

        self._task_metrics[effective_task_id] = metrics
        self._provider_metrics_by_task[effective_task_id] = self._compute_provider_metrics_from_state(state)

    def get_task_metrics(self, task_id: str) -> Optional[TaskMetrics]:
        """Retrieve stored metrics for a task."""
        return self._task_metrics.get(str(task_id or ""))

    def get_aggregate_metrics(self) -> AggregateMetrics:
        """Compute aggregate metrics across all recorded tasks."""
        metrics_list = list(self._task_metrics.values())
        if not metrics_list:
            return AggregateMetrics(
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                completion_rate=0.0,
                avg_quality_score=0.0,
                avg_recovery_rate=0.0,
                avg_latency_ms=0.0,
                total_tokens_consumed=0,
                total_cost_usd=0.0,
                provider_metrics={},
            )

        total_tasks = len(metrics_list)
        completed_tasks = sum(1 for metric in metrics_list if metric.status.lower() in {"completed", "success"})
        failed_tasks = sum(1 for metric in metrics_list if metric.status.lower() == "failed")

        completion_rate = completed_tasks / total_tasks if total_tasks > 0 else 0.0
        avg_quality_score = (
            sum(self._task_avg_quality(metric) for metric in metrics_list) / total_tasks if total_tasks > 0 else 0.0
        )
        avg_recovery_rate = (
            sum(self._task_recovery_rate(metric) for metric in metrics_list) / total_tasks if total_tasks > 0 else 0.0
        )
        avg_latency_ms = (
            sum(float(metric.total_duration_ms) for metric in metrics_list) / total_tasks if total_tasks > 0 else 0.0
        )

        total_tokens_consumed = sum(metric.total_tokens for metric in metrics_list)
        total_cost_usd = sum(float(metric.estimated_cost_usd) for metric in metrics_list)

        return AggregateMetrics(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            completion_rate=round(completion_rate, 4),
            avg_quality_score=round(avg_quality_score, 4),
            avg_recovery_rate=round(avg_recovery_rate, 4),
            avg_latency_ms=round(avg_latency_ms, 2),
            total_tokens_consumed=total_tokens_consumed,
            total_cost_usd=round(total_cost_usd, 8),
            provider_metrics=self._aggregate_provider_metrics(),
        )

    def get_trace_summary(self, state: AgentState) -> TraceSummary:
        """Compute trace summary statistics."""
        state_data = dict(state or {})
        task_id = str(state_data.get("task_id") or "")
        trace = self._normalize_trace(state_data.get("execution_trace"))
        step_results = self._as_list(state_data.get("step_results"))
        steps = self._as_list(state_data.get("steps"))

        events_by_type = Counter(str(event.get("event_type") or "unknown") for event in trace)
        retry_count = int(events_by_type.get("retry_triggered", 0))
        fallback_count = int(events_by_type.get("fallback_triggered", 0))
        reflection_count = int(events_by_type.get("reflection_started", 0) + events_by_type.get("reflection_completed", 0))

        timestamps = [
            parsed
            for parsed in (self._parse_iso8601(event.get("timestamp")) for event in trace)
            if parsed is not None
        ]

        timeline_start = timestamps[0].isoformat() if timestamps else ""
        timeline_end = timestamps[-1].isoformat() if timestamps else ""

        total_duration_ms = 0
        if len(timestamps) >= 2:
            total_duration_ms = max(0, int((timestamps[-1] - timestamps[0]).total_seconds() * 1000))
        elif timestamps:
            total_duration_ms = max(0, self._to_int(trace[-1].get("duration_ms")))

        if total_duration_ms <= 0:
            total_duration_ms = self._compute_duration_ms(
                started_at=state_data.get("started_at"),
                completed_at=state_data.get("completed_at"),
            )

        step_name_map: dict[str, str] = {}
        for step in steps:
            step_id = self._normalize_optional_string(self._get_field(step, "step_id"))
            if not step_id:
                continue
            step_name_map[step_id] = self._normalize_optional_string(self._get_field(step, "name")) or step_id

        step_durations: list[dict[str, Any]] = []
        for result in step_results:
            step_id = self._normalize_optional_string(self._get_field(result, "step_id"))
            if not step_id:
                continue

            step_durations.append(
                {
                    "step_id": step_id,
                    "step_name": step_name_map.get(step_id, step_id),
                    "duration_ms": max(0, self._to_int(self._get_field(result, "latency_ms"))),
                    "status": self._result_status(result),
                }
            )

        return TraceSummary(
            task_id=task_id,
            total_events=len(trace),
            events_by_type=dict(events_by_type),
            timeline_start=timeline_start,
            timeline_end=timeline_end,
            total_duration_ms=total_duration_ms,
            step_durations=step_durations,
            retry_count=retry_count,
            fallback_count=fallback_count,
            reflection_count=reflection_count,
        )

    def _compute_token_breakdown(
        self,
        trace: list[dict[str, Any]],
        step_results: list[Any],
        llm_tokens_used: int,
    ) -> tuple[int, int]:
        """Compute input/output token split from trace metadata or fallback estimates."""
        tokens_input = 0
        tokens_output = 0

        for event in trace:
            details = dict(event.get("details") or {})
            tokens_input += max(0, self._to_int(event.get("tokens_in", details.get("tokens_in"))))
            tokens_output += max(0, self._to_int(event.get("tokens_out", details.get("tokens_out"))))

        if tokens_input > 0 or tokens_output > 0:
            return tokens_input, tokens_output

        total_tokens = 0
        for result in step_results:
            total_tokens += max(0, self._to_int(self._get_field(result, "tokens_used")))

        if total_tokens <= 0:
            total_tokens = max(0, llm_tokens_used)

        estimated_input = total_tokens // 2
        estimated_output = total_tokens - estimated_input
        return estimated_input, estimated_output

    def _estimate_cost_usd(
        self,
        step_results: list[Any],
        trace: list[dict[str, Any]],
        fallback_total_tokens: int,
    ) -> float:
        """Estimate total model spend using model-specific input/output rates."""
        tokens_by_model: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})

        for event in trace:
            details = dict(event.get("details") or {})
            model_name = self._normalize_optional_string(
                event.get("model_used") or details.get("model_used")
            )
            if not model_name:
                continue

            tokens_in = max(0, self._to_int(event.get("tokens_in", details.get("tokens_in"))))
            tokens_out = max(0, self._to_int(event.get("tokens_out", details.get("tokens_out"))))

            if tokens_in == 0 and tokens_out == 0:
                total_tokens = max(0, self._to_int(event.get("tokens_used", details.get("tokens_used"))))
                tokens_in = total_tokens // 2
                tokens_out = total_tokens - tokens_in

            tokens_by_model[model_name]["input"] += tokens_in
            tokens_by_model[model_name]["output"] += tokens_out

        if not tokens_by_model:
            for result in step_results:
                model_name = self._normalize_optional_string(self._get_field(result, "model_used"))
                if not model_name:
                    continue

                total_tokens = max(0, self._to_int(self._get_field(result, "tokens_used")))
                tokens_in = total_tokens // 2
                tokens_out = total_tokens - tokens_in
                tokens_by_model[model_name]["input"] += tokens_in
                tokens_by_model[model_name]["output"] += tokens_out

        if not tokens_by_model and fallback_total_tokens > 0:
            # Last-resort estimate when model metadata is absent.
            tokens_by_model["gpt-4o"]["input"] += fallback_total_tokens // 2
            tokens_by_model["gpt-4o"]["output"] += fallback_total_tokens - (fallback_total_tokens // 2)

        total_cost = 0.0
        for model_name, token_split in tokens_by_model.items():
            price_key = self._model_pricing_key(model_name)
            rates = MODEL_COST_RATES_USD_PER_MILLION.get(price_key)
            if rates is None:
                continue

            total_cost += (token_split["input"] / 1_000_000) * rates["input"]
            total_cost += (token_split["output"] / 1_000_000) * rates["output"]

        return round(total_cost, 8)

    def _collect_models_used(self, step_results: list[Any], trace: list[dict[str, Any]]) -> Dict[str, int]:
        """Count model call frequency from step results, then trace as fallback."""
        counter: Counter[str] = Counter()

        for result in step_results:
            model_name = self._normalize_optional_string(self._get_field(result, "model_used"))
            if model_name:
                counter[model_name] += 1

        if counter:
            return dict(counter)

        for event in trace:
            details = dict(event.get("details") or {})
            model_name = self._normalize_optional_string(
                event.get("model_used") or details.get("model_used")
            )
            if model_name:
                counter[model_name] += 1

        return dict(counter)

    def _collect_tools_used(self, step_results: list[Any], trace: list[dict[str, Any]]) -> Dict[str, int]:
        """Count tool usage frequency for task-level analysis."""
        counter: Counter[str] = Counter()

        for result in step_results:
            tool_name = self._normalize_optional_string(self._get_field(result, "tool_used"))
            if tool_name:
                counter[tool_name] += 1

        if counter:
            return dict(counter)

        for event in trace:
            details = dict(event.get("details") or {})
            tool_name = self._normalize_optional_string(details.get("tool_used"))
            if tool_name:
                counter[tool_name] += 1

        return dict(counter)

    def _collect_agents_used(self, step_results: list[Any], trace: list[dict[str, Any]]) -> Dict[str, int]:
        """Count per-agent handled steps using result metadata and trace hints."""
        counter: Counter[str] = Counter()
        step_to_agent: dict[str, str] = {}

        for result in step_results:
            step_id = self._normalize_optional_string(self._get_field(result, "step_id"))
            agent_name = self._normalize_optional_string(self._get_field(result, "agent_name"))
            if step_id and agent_name:
                step_to_agent.setdefault(step_id, agent_name)

        for event in trace:
            details = dict(event.get("details") or {})
            step_id = self._normalize_optional_string(event.get("step_id"))
            agent_name = self._normalize_optional_string(event.get("agent_name") or details.get("agent_name"))
            if step_id and agent_name:
                step_to_agent.setdefault(step_id, agent_name)

        for agent_name in step_to_agent.values():
            counter[agent_name] += 1

        return dict(counter)

    def _collect_quality_scores(self, error_log: list[Any]) -> list[dict[str, Any]]:
        """Collect latest validator quality scores per step from error log."""
        latest_per_step: dict[str, dict[str, Any]] = {}

        for entry in error_log:
            if not isinstance(entry, dict):
                continue

            step_id = self._normalize_optional_string(entry.get("step_id"))
            if not step_id:
                continue

            scores = entry.get("scores")
            if not isinstance(scores, dict):
                continue

            latest_per_step[step_id] = {
                "step_id": step_id,
                "relevance": self._to_int(scores.get("relevance")),
                "completeness": self._to_int(scores.get("completeness")),
                "consistency": self._to_int(scores.get("consistency")),
                "plausibility": self._to_int(scores.get("plausibility")),
            }

        return list(latest_per_step.values())

    def _collect_failure_types(self, error_log: list[Any], step_results: list[Any]) -> Dict[str, int]:
        """Build histogram of failure categories from error logs and fallback step errors."""
        counter: Counter[str] = Counter()

        for entry in error_log:
            if not isinstance(entry, dict):
                continue
            error_type = self._normalize_optional_string(entry.get("error_type"))
            if error_type:
                counter[error_type] += 1

        if counter:
            return dict(counter)

        for result in step_results:
            if self._result_status(result) != "failed":
                continue
            error_text = self._normalize_optional_string(self._get_field(result, "error"))
            if error_text:
                counter[error_text] += 1
            else:
                counter["EXECUTION_FAILURE"] += 1

        return dict(counter)

    def _collect_reflection_strategies(self, trace: list[dict[str, Any]]) -> Dict[str, int]:
        """Build histogram of reflection actions chosen by reflector."""
        counter: Counter[str] = Counter()

        for event in trace:
            if str(event.get("event_type") or "") != "reflection_completed":
                continue

            details = dict(event.get("details") or {})
            action = self._normalize_optional_string(details.get("action"))
            if action:
                counter[action] += 1

        return dict(counter)

    def _estimate_parallel_time_saved(
        self,
        state_data: dict[str, Any],
        trace: list[dict[str, Any]],
        total_step_runtime_ms: int,
        wall_clock_ms: int,
    ) -> Optional[int]:
        """Estimate parallel execution savings when parallel metadata is present."""
        has_parallel_metadata = bool(state_data.get("execution_levels")) or any(
            event.get("level") is not None for event in trace
        )
        if not has_parallel_metadata:
            return None

        if total_step_runtime_ms <= 0 or wall_clock_ms <= 0:
            return 0

        return max(0, total_step_runtime_ms - wall_clock_ms)

    def _compute_provider_metrics_from_state(self, state: AgentState) -> Dict[str, dict[str, Any]]:
        """Compute provider-level health/call metrics for a single task state."""
        state_data = dict(state or {})
        trace = self._normalize_trace(state_data.get("execution_trace"))
        step_results = self._as_list(state_data.get("step_results"))

        stats: dict[str, dict[str, Any]] = {}

        def _entry(provider: str) -> dict[str, Any]:
            normalized = provider.strip().lower()
            if normalized not in stats:
                stats[normalized] = {
                    "calls": 0,
                    "failures": 0,
                    "latency_sum": 0,
                    "latency_count": 0,
                    "circuit_state": "closed",
                }
            return stats[normalized]

        for event in trace:
            event_type = str(event.get("event_type") or "")
            details = dict(event.get("details") or {})

            if event_type == "fallback_triggered":
                from_provider = self._normalize_optional_string(
                    event.get("from_provider") or details.get("from_provider")
                )
                to_provider = self._normalize_optional_string(
                    event.get("to_provider") or details.get("to_provider")
                )

                if from_provider:
                    _entry(from_provider)["failures"] += 1
                if to_provider:
                    _entry(to_provider)["calls"] += 1
                continue

            provider = self._normalize_optional_string(event.get("provider") or details.get("provider"))
            if not provider:
                model_name = self._normalize_optional_string(event.get("model_used") or details.get("model_used"))
                provider = self._infer_provider_from_model(model_name)
            if not provider:
                continue

            provider_stats = _entry(provider)
            if event_type in {"step_completed", "step_failed"}:
                provider_stats["calls"] += 1
            if event_type == "step_failed":
                provider_stats["failures"] += 1

            duration_ms = max(0, self._to_int(event.get("duration_ms")))
            if duration_ms > 0:
                provider_stats["latency_sum"] += duration_ms
                provider_stats["latency_count"] += 1

            circuit_state = self._normalize_optional_string(event.get("circuit_state") or details.get("circuit_state"))
            if circuit_state:
                provider_stats["circuit_state"] = circuit_state

        if not stats:
            for result in step_results:
                model_name = self._normalize_optional_string(self._get_field(result, "model_used"))
                provider = self._infer_provider_from_model(model_name)
                if not provider:
                    continue

                provider_stats = _entry(provider)
                provider_stats["calls"] += 1
                if self._result_status(result) == "failed":
                    provider_stats["failures"] += 1

                duration_ms = max(0, self._to_int(self._get_field(result, "latency_ms")))
                if duration_ms > 0:
                    provider_stats["latency_sum"] += duration_ms
                    provider_stats["latency_count"] += 1

        for provider, provider_stats in stats.items():
            latency_count = max(1, self._to_int(provider_stats.get("latency_count")))
            provider_stats["avg_latency"] = round(provider_stats.get("latency_sum", 0) / latency_count, 2)
            provider_stats["provider"] = provider

        return stats

    def _aggregate_provider_metrics(self) -> Dict[str, dict[str, Any]]:
        """Merge provider metrics across all recorded task snapshots."""
        merged: dict[str, dict[str, Any]] = {}

        for provider_map in self._provider_metrics_by_task.values():
            for provider, metrics in provider_map.items():
                if provider not in merged:
                    merged[provider] = {
                        "calls": 0,
                        "failures": 0,
                        "latency_sum": 0,
                        "latency_count": 0,
                        "circuit_state": "closed",
                    }

                merged[provider]["calls"] += self._to_int(metrics.get("calls"))
                merged[provider]["failures"] += self._to_int(metrics.get("failures"))
                merged[provider]["latency_sum"] += self._to_int(metrics.get("latency_sum"))
                merged[provider]["latency_count"] += self._to_int(metrics.get("latency_count"))

                circuit_state = self._normalize_optional_string(metrics.get("circuit_state"))
                if circuit_state:
                    merged[provider]["circuit_state"] = circuit_state

        finalized: dict[str, dict[str, Any]] = {}
        for provider, metrics in merged.items():
            calls = max(0, self._to_int(metrics.get("calls")))
            failures = max(0, self._to_int(metrics.get("failures")))
            latency_count = max(1, self._to_int(metrics.get("latency_count")))
            avg_latency = float(metrics.get("latency_sum", 0)) / latency_count

            finalized[provider] = {
                "calls": calls,
                "failures": failures,
                "avg_latency": round(avg_latency, 2),
                "circuit_state": self._normalize_optional_string(metrics.get("circuit_state")) or "closed",
            }

        return finalized

    def _task_avg_quality(self, metric: TaskMetrics) -> float:
        """Compute average quality score across all quality dimensions in a task."""
        values: list[float] = []
        for entry in metric.quality_scores:
            if not isinstance(entry, dict):
                continue
            for key in ("relevance", "completeness", "consistency", "plausibility"):
                values.append(float(self._to_int(entry.get(key))))

        if not values:
            return 0.0
        return sum(values) / len(values)

    def _task_recovery_rate(self, metric: TaskMetrics) -> float:
        """Estimate recovery effectiveness based on remediation activity vs residual failures."""
        recovery_actions = metric.retry_count + metric.fallback_count + metric.reflection_count
        if recovery_actions <= 0:
            return 1.0 if metric.status.lower() in {"completed", "success"} else 0.0

        unresolved_failures = max(0, metric.failed_steps)
        recovered_actions = max(0, recovery_actions - unresolved_failures)
        return min(1.0, recovered_actions / max(1, recovery_actions))

    def _normalize_trace(self, raw_trace: Any) -> list[dict[str, Any]]:
        """Normalize trace entries into plain dictionaries."""
        entries = self._as_list(raw_trace)
        normalized: list[dict[str, Any]] = []

        for entry in entries:
            if hasattr(entry, "model_dump"):
                dump = entry.model_dump()
                if isinstance(dump, dict):
                    normalized.append(dump)
                continue

            if isinstance(entry, dict):
                normalized.append(dict(entry))

        normalized.sort(
            key=lambda item: self._parse_iso8601(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
        )
        return normalized

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        """Return list values or an empty list for non-list payloads."""
        return value if isinstance(value, list) else []

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Best-effort integer conversion helper."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_optional_string(value: Any) -> Optional[str]:
        """Normalize optional string payload values."""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized if normalized else None

    @staticmethod
    def _get_field(item: Any, field_name: str) -> Any:
        """Read a field from a dict-like or object-like payload."""
        if isinstance(item, dict):
            return item.get(field_name)
        return getattr(item, field_name, None)

    @staticmethod
    def _result_status(result: Any) -> str:
        """Normalize step-result status values."""
        status = MetricsService._normalize_optional_string(MetricsService._get_field(result, "status"))
        return status.lower() if status else "failed"

    @staticmethod
    def _parse_iso8601(value: Any) -> Optional[datetime]:
        """Parse ISO 8601 timestamps safely with timezone normalization."""
        if not isinstance(value, str) or not value.strip():
            return None

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    def _compute_duration_ms(self, started_at: Any, completed_at: Any) -> int:
        """Compute wall-clock duration in milliseconds from task boundaries."""
        started = self._parse_iso8601(started_at)
        completed = self._parse_iso8601(completed_at)

        if started is None:
            return 0
        if completed is None:
            completed = datetime.now(timezone.utc)

        return max(0, int((completed - started).total_seconds() * 1000))

    @staticmethod
    def _model_pricing_key(model_name: str) -> str:
        """Normalize model names to pricing-table keys."""
        normalized = (model_name or "").strip().lower()
        if normalized.startswith("gpt-4o-mini"):
            return "gpt-4o-mini"
        if normalized.startswith("gpt-4o"):
            return "gpt-4o"
        if "claude" in normalized and ("3.5" in normalized or "3-5" in normalized or "sonnet" in normalized):
            return "claude-3.5"
        return ""

    @staticmethod
    def _infer_provider_from_model(model_name: Optional[str]) -> Optional[str]:
        """Infer provider from model naming conventions."""
        if not model_name:
            return None

        normalized = model_name.strip().lower()
        if normalized.startswith("gpt"):
            return "openai"
        if normalized.startswith("claude"):
            return "anthropic"
        if normalized:
            return "open_source"
        return None


_service: MetricsService | None = None


def get_metrics_service() -> MetricsService:
    """Return a process-wide singleton metrics service instance."""
    global _service
    if _service is None:
        _service = MetricsService()
    return _service
