"""Public exports for multi-agent specialization components."""

from .agents import AGENT_REGISTRY, SpecializedAgent
from .coordinator import AgentCoordinator
from .router import AgentRouter

__all__ = [
    "SpecializedAgent",
    "AgentRouter",
    "AgentCoordinator",
    "AGENT_REGISTRY",
]
