"""Agent routing logic for selecting specialized execution agents."""

from __future__ import annotations

import re
from typing import Dict, Optional

from models import StepDefinition
from services.llm_service import LLMError, call_llm

from .agents import AGENT_REGISTRY, ANALYSIS_AGENT, SpecializedAgent


class AgentRouter:
    """Routes steps to the most appropriate specialized agent."""

    TOOL_AGENT_MAP = {
        "web_search": "research",
        "api_call": "research",
        "code_exec": "code",
    }

    ROLE_KEYWORDS: Dict[str, list[str]] = {
        "research": ["search", "find", "look up", "research", "investigate", "discover", "gather"],
        "code": ["write code", "implement", "program", "function", "class", "test", "debug", "script"],
        "analysis": ["analyze", "compare", "evaluate", "assess", "review", "examine", "contrast", "pros and cons"],
        "writing": ["write", "summarize", "report", "document", "explain", "describe", "compile", "draft"],
    }

    async def route_step(self, step: StepDefinition) -> SpecializedAgent:
        """Determine which agent should handle this step.

        Routing priority:
        1. Tool-based: if step.tool_needed maps directly -> use that agent
        2. Keyword-based: scan step.description for role keywords -> highest match
        3. LLM classification: if ambiguous, ask LLM to classify
        4. Default: ANALYSIS_AGENT (generalist fallback)

        Returns: SpecializedAgent instance
        """
        tool = str(step.tool_needed or "").strip().lower()
        role_from_tool = self.TOOL_AGENT_MAP.get(tool)
        if role_from_tool in AGENT_REGISTRY:
            return AGENT_REGISTRY[role_from_tool]

        combined_text = f"{step.name}\n{step.description}"
        role_from_keywords = self._keyword_classify(combined_text)
        if role_from_keywords and role_from_keywords in AGENT_REGISTRY:
            return AGENT_REGISTRY[role_from_keywords]

        role_from_llm = await self._llm_classify(step)
        if role_from_llm in AGENT_REGISTRY:
            return AGENT_REGISTRY[role_from_llm]

        return ANALYSIS_AGENT

    def _keyword_classify(self, description: str) -> Optional[str]:
        """Score step description against role keywords. Return role with highest score."""
        text = str(description or "").strip().lower()
        if not text:
            return None

        scores: Dict[str, int] = {role: 0 for role in self.ROLE_KEYWORDS}

        for role, keywords in self.ROLE_KEYWORDS.items():
            for keyword in keywords:
                candidate = keyword.lower().strip()
                if not candidate:
                    continue

                if " " in candidate:
                    if candidate in text:
                        scores[role] += 2
                    continue

                matches = re.findall(rf"\b{re.escape(candidate)}\b", text)
                scores[role] += len(matches)

        best_score = max(scores.values()) if scores else 0
        if best_score <= 0:
            return None

        winners = [role for role, score in scores.items() if score == best_score]
        if len(winners) != 1:
            return None

        return winners[0]

    async def _llm_classify(self, step: StepDefinition) -> str:
        """Use LLM to classify ambiguous steps.
        Prompt: 'Classify this step into one of: research, code, analysis, writing.
                 Step: {description}
                 Respond with a single word.'
        Use lightweight open-source model for classification."""
        prompt = (
            "Classify this step into exactly one category: research, code, analysis, writing.\n"
            f"Step name: {step.name}\n"
            f"Step description: {step.description}\n"
            "Respond with a single lowercase word only."
        )

        try:
            response = await call_llm(
                prompt=prompt,
                system_prompt="You classify task steps into specialized execution roles.",
                model="Qwen/Qwen2.5-7B-Instruct",
                provider="open_source",
                temperature=0.0,
                max_tokens=12,
                json_mode=False,
                timeout=20,
            )
            text = (response.text or "").strip().lower()
            match = re.search(r"\b(research|code|analysis|writing)\b", text)
            if match:
                return match.group(1)
        except LLMError:
            pass
        except Exception:
            pass

        return "analysis"
