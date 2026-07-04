"""Coordinator for multi-agent routing, execution, and contribution tracking."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from agent.reliability.circuit_breaker import get_circuit_breaker_manager
from agent.reliability.fallback import FALLBACK_CHAIN, call_with_fallback
from agent.state import AgentState
from config import get_settings
from models import StepDefinition, StepResult
from services.llm_service import call_llm

from .agents import AGENT_REGISTRY, ANALYSIS_AGENT, SpecializedAgent
from .router import AgentRouter

logger = logging.getLogger(__name__)


class AgentCoordinator:
    """Orchestrates multi-agent task execution."""

    def __init__(self):
        self.router = AgentRouter()
        self.agent_history: Dict[str, List[dict]] = {}
        self.agent_stats: Dict[str, dict] = {}

    async def execute_step_with_agent(
        self,
        step: StepDefinition,
        state: AgentState,
    ) -> Tuple[StepResult, SpecializedAgent]:
        """Route step, execute via specialized agent profile, and track assignment/metrics."""
        agent = await self.router.route_step(step)
        context = self._build_context(state=state, current_step_id=step.step_id)
        system_prompt, user_prompt = self.build_agent_prompt(agent=agent, step=step, context=context)

        previous_agent = self._resolve_previous_agent(state)
        handoff_summary = ""
        if previous_agent is not None and previous_agent.role != agent.role:
            prior_output = self._latest_output(state)
            handoff_summary = await self.create_handoff_summary(
                from_agent=previous_agent,
                to_agent=agent,
                prior_result=prior_output,
            )
            if handoff_summary:
                user_prompt = (
                    f"{user_prompt}\n\n"
                    "Cross-agent handoff context (from previous specialist):\n"
                    f"{handoff_summary}"
                )

        retry_count = int(state.get("retry_counts", {}).get(step.step_id, 0))

        try:
            response = await call_with_fallback(
                prompt=user_prompt,
                system_prompt=system_prompt,
                json_mode=False,
                temperature=agent.temperature,
                max_tokens=4096,
                timeout=max(1, int(get_settings().STEP_TIMEOUT)),
                task_id=state.get("task_id"),
                step_id=step.step_id,
                fallback_chain=self._build_runtime_fallback_chain(agent.preferred_model),
                circuit_breaker=get_circuit_breaker_manager(),
            )

            result = StepResult(
                step_id=step.step_id,
                status="success",
                output=(response.text or "").strip(),
                tokens_used=max(0, int(response.tokens_used)),
                latency_ms=max(0, int(response.latency_ms)),
                model_used=str(response.model_used or agent.preferred_model),
                tool_used=step.tool_needed if step.tool_needed != "none" else None,
                tool_result=None,
                retry_count=retry_count,
                agent_name=agent.name,
                agent_role=agent.role,
            )

            self._record_assignment(
                step_id=step.step_id,
                agent=agent,
                action="executed",
                result_status=result.status,
                tokens_used=result.tokens_used,
                routing_reason=self._routing_reason(step=step, agent=agent),
                handoff_summary=handoff_summary,
            )
            self._update_stats(agent=agent, tokens_used=result.tokens_used, quality_score=None)
            self._apply_state_metrics(state=state, step_id=step.step_id)

            return result, agent
        except Exception as exc:
            logger.warning(
                "agent_coordinator_execution_failed task_id=%s step_id=%s agent=%s error=%s",
                state.get("task_id"),
                step.step_id,
                agent.name,
                exc,
            )
            failure = StepResult(
                step_id=step.step_id,
                status="failed",
                output="",
                tokens_used=0,
                latency_ms=0,
                model_used=agent.preferred_model,
                tool_used=step.tool_needed if step.tool_needed != "none" else None,
                tool_result=None,
                retry_count=retry_count,
                error=str(exc),
                agent_name=agent.name,
                agent_role=agent.role,
            )
            self._record_assignment(
                step_id=step.step_id,
                agent=agent,
                action="failed",
                result_status=failure.status,
                tokens_used=0,
                routing_reason=self._routing_reason(step=step, agent=agent),
                handoff_summary=handoff_summary,
            )
            self._apply_state_metrics(state=state, step_id=step.step_id)
            return failure, agent

    def build_agent_prompt(
        self,
        agent: SpecializedAgent,
        step: StepDefinition,
        context: str,
    ) -> Tuple[str, str]:
        """Build system prompt and user prompt for the agent."""
        system_prompt = (
            f"{agent.system_prompt}\n\n"
            f"Current role: {agent.role}\n"
            f"Preferred model profile: {agent.preferred_model}\n"
            "Maintain high factual precision and preserve context continuity with prior steps."
        )

        user_prompt = (
            f"Overall task:\n{step.description}\n\n"
            f"Step id: {step.step_id}\n"
            f"Step name: {step.name}\n"
            f"Step instructions:\n{step.description}\n\n"
            f"Allowed tools for this role: {', '.join(agent.tools)}\n"
            "Use only available context and produce a complete answer for this step.\n\n"
            f"Prior execution context:\n{context}"
        )
        return system_prompt, user_prompt

    async def create_handoff_summary(
        self,
        from_agent: SpecializedAgent,
        to_agent: SpecializedAgent,
        prior_result: str,
    ) -> str:
        """Create a bridge summary that translates context between agent roles."""
        if not prior_result.strip():
            return "No prior output was available for handoff."

        prompt = (
            "Create a concise handoff summary between specialized agents.\n"
            f"From role: {from_agent.role}\n"
            f"To role: {to_agent.role}\n"
            "Summarize what matters for the receiving role, preserving key facts, assumptions, and open questions.\n"
            "Keep it under 120 words.\n\n"
            f"Prior result:\n{prior_result[:2000]}"
        )

        try:
            response = await call_llm(
                prompt=prompt,
                system_prompt="You produce concise cross-role handoff summaries.",
                model="Qwen/Qwen2.5-7B-Instruct",
                provider="open_source",
                temperature=0.2,
                max_tokens=220,
                json_mode=False,
                timeout=25,
            )
            summary = (response.text or "").strip()
            return summary or prior_result[:500]
        except Exception:
            return prior_result[:500]

    def get_agent_contributions(self) -> Dict[str, dict]:
        """Return per-agent statistics for dashboard consumption."""
        contributions: Dict[str, dict] = {}
        for agent_name, stats in self.agent_stats.items():
            steps_handled = int(stats.get("steps_handled", 0))
            total_tokens = int(stats.get("total_tokens", 0))
            total_quality = float(stats.get("total_quality", 0.0))
            quality_samples = int(stats.get("quality_samples", 0))
            avg_quality = (total_quality / quality_samples) if quality_samples > 0 else 0.0
            roles_executed = sorted(list(stats.get("roles_executed", set())))

            contributions[agent_name] = {
                "steps_handled": steps_handled,
                "avg_quality": round(avg_quality, 3),
                "total_tokens": total_tokens,
                "roles_executed": roles_executed,
            }
        return contributions

    def get_assignment_log(self) -> List[dict]:
        """Return chronological agent assignment log for trace."""
        records: List[dict] = []
        for step_records in self.agent_history.values():
            records.extend(step_records)

        records.sort(key=lambda entry: str(entry.get("timestamp", "")))
        return records

    def _record_assignment(
        self,
        step_id: str,
        agent: SpecializedAgent,
        action: str,
        result_status: str,
        tokens_used: int,
        routing_reason: str,
        handoff_summary: str,
    ) -> None:
        """Store assignment metadata for trace/review."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step_id,
            "agent_name": agent.name,
            "agent_role": agent.role,
            "action": action,
            "result_status": result_status,
            "tokens_used": max(0, int(tokens_used)),
            "routing_reason": routing_reason,
            "handoff_summary": handoff_summary,
        }
        self.agent_history.setdefault(step_id, []).append(entry)

    def _update_stats(self, agent: SpecializedAgent, tokens_used: int, quality_score: Optional[float]) -> None:
        """Update aggregate per-agent counters."""
        stats = self.agent_stats.setdefault(
            agent.name,
            {
                "steps_handled": 0,
                "total_tokens": 0,
                "total_quality": 0.0,
                "quality_samples": 0,
                "roles_executed": set(),
            },
        )
        stats["steps_handled"] += 1
        stats["total_tokens"] += max(0, int(tokens_used))
        stats["roles_executed"].add(agent.role)

        if quality_score is not None:
            stats["total_quality"] += float(quality_score)
            stats["quality_samples"] += 1

    def _apply_state_metrics(self, state: AgentState, step_id: str) -> None:
        """Expose assignment and contribution aggregates in AgentState."""
        if "agent_assignments" not in state:
            state["agent_assignments"] = {}
        if "agent_contributions" not in state:
            state["agent_contributions"] = {}

        latest_assignment = self.agent_history.get(step_id, [])
        if latest_assignment:
            state["agent_assignments"][step_id] = str(latest_assignment[-1].get("agent_name", ""))

        state["agent_contributions"] = self.get_agent_contributions()

    def _build_context(self, state: AgentState, current_step_id: str) -> str:
        """Create compact execution context for routed agents."""
        prior_results = [
            result
            for result in state.get("step_results", [])[-5:]
            if result.step_id != current_step_id
        ]
        result_lines = [
            f"- {result.step_id} ({result.status}): {(result.output or '')[:400]}"
            for result in prior_results
        ]
        if not result_lines:
            result_lines = ["- No prior step results available."]

        memory_lines = [f"- {entry}" for entry in state.get("context_memory", [])[-5:]]
        if not memory_lines:
            memory_lines = ["- No additional memory context available."]

        return "\n".join(result_lines + ["", "Memory context:"] + memory_lines)

    def _resolve_previous_agent(self, state: AgentState) -> Optional[SpecializedAgent]:
        """Resolve the previously assigned agent from state, if any."""
        assignments = state.get("agent_assignments") or {}
        if not assignments:
            return None

        prior_step_id = ""
        for result in reversed(state.get("step_results", [])):
            if result.step_id in assignments:
                prior_step_id = result.step_id
                break
        if not prior_step_id:
            return None

        prior_agent_name = str(assignments.get(prior_step_id, "")).strip()
        for agent in AGENT_REGISTRY.values():
            if agent.name == prior_agent_name:
                return agent
        return None

    @staticmethod
    def _latest_output(state: AgentState) -> str:
        """Get latest non-empty output for handoff generation."""
        for result in reversed(state.get("step_results", [])):
            if (result.output or "").strip():
                return result.output
        return ""

    @staticmethod
    def _routing_reason(step: StepDefinition, agent: SpecializedAgent) -> str:
        """Build a compact routing reason string used in trace events."""
        tool = str(step.tool_needed or "").strip().lower()
        if tool in {"web_search", "api_call", "code_exec"}:
            return f"tool_based:{tool}"
        return f"semantic_classification:{agent.role}"

    @staticmethod
    def _build_runtime_fallback_chain(preferred_model: str) -> List[dict]:
        """Build runtime fallback chain with preferred model as first candidate."""
        primary_provider = AgentCoordinator._provider_for_model(preferred_model)
        chain: List[dict] = [
            {
                "provider": primary_provider,
                "model": preferred_model,
                "label": "Agent Preferred",
            }
        ]

        seen = {(primary_provider, preferred_model)}
        for entry in FALLBACK_CHAIN:
            provider = str(entry.get("provider", "")).strip().lower()
            model = str(entry.get("model", "")).strip()
            label = str(entry.get("label", f"{provider}/{model}")).strip()
            pair = (provider, model)
            if not provider or not model or pair in seen:
                continue
            chain.append({"provider": provider, "model": model, "label": label})
            seen.add(pair)

        return chain

    @staticmethod
    def _provider_for_model(model_name: str) -> str:
        """Infer provider from model naming conventions."""
        normalized = str(model_name or "").strip().lower()
        if normalized.startswith("claude"):
            return "anthropic"
        if normalized.startswith("gpt"):
            return "openai"
        return "open_source"


_coordinator: AgentCoordinator | None = None


def get_agent_coordinator() -> AgentCoordinator:
    """Return process-wide singleton coordinator instance."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator
