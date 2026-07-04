"""Chaos middleware used to inject reliability test failures."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from config import get_settings
from services.llm_service import LLMRateLimitError, LLMResponseError

logger = logging.getLogger(__name__)


class ChaosMiddleware:
    """Injects random failures when CHAOS_MODE is enabled."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.force_next_rate_limit = bool(enabled)
        self.injection_stats = {
            "latency": 0,
            "empty": 0,
            "rate_limit": 0,
            "corrupt": 0,
            "passthrough": 0,
        }

    async def maybe_inject(self, context: str = "") -> Optional[str]:
        """
        Call before each external call. Returns None if no injection,
        or raises an appropriate exception if injecting.

        Injection probabilities:
        - 30%: Add 5s latency
        - 20%: Raise LLMResponseError("Chaos: empty response")
        - 15%: Raise LLMRateLimitError("Chaos: rate limit")
        - 10%: Return "CORRUPT"
        - 25%: Pass through (no injection)
        """
        if not self.enabled:
            return None

        if self.force_next_rate_limit:
            self.force_next_rate_limit = False
            self.injection_stats["rate_limit"] += 1
            logger.warning("CHAOS: Injected forced rate_limit at %s", context or "unknown")
            raise _tag_chaos_exception(LLMRateLimitError("Chaos: forced rate limit"), "rate_limit")

        roll = random.random()
        context_text = context or "unknown"

        if roll < 0.30:
            self.injection_stats["latency"] += 1
            logger.warning("CHAOS: Injected latency at %s", context_text)
            await asyncio.sleep(5)
            return None

        if roll < 0.50:
            self.injection_stats["empty"] += 1
            logger.warning("CHAOS: Injected empty at %s", context_text)
            raise _tag_chaos_exception(LLMResponseError("Chaos: empty response"), "empty")

        if roll < 0.65:
            self.injection_stats["rate_limit"] += 1
            logger.warning("CHAOS: Injected rate_limit at %s", context_text)
            raise _tag_chaos_exception(LLMRateLimitError("Chaos: rate limit"), "rate_limit")

        if roll < 0.75:
            self.injection_stats["corrupt"] += 1
            logger.warning("CHAOS: Injected corrupt at %s", context_text)
            return "CORRUPT"

        self.injection_stats["passthrough"] += 1
        logger.warning("CHAOS: Injected passthrough at %s", context_text)
        return None

    def get_stats(self) -> dict:
        """Return injection statistics."""
        return dict(self.injection_stats)

    def reset_stats(self):
        """Reset statistics."""
        for key in self.injection_stats:
            self.injection_stats[key] = 0


_chaos_middleware: ChaosMiddleware | None = None


def get_chaos_middleware() -> ChaosMiddleware:
    """Return process-wide chaos middleware singleton."""
    global _chaos_middleware
    if _chaos_middleware is None:
        settings = get_settings()
        _chaos_middleware = ChaosMiddleware(enabled=bool(settings.CHAOS_MODE))
    return _chaos_middleware


def set_chaos_mode(enabled: bool) -> ChaosMiddleware:
    """Update runtime chaos mode flag and return middleware instance."""
    settings = get_settings()
    settings.CHAOS_MODE = bool(enabled)

    middleware = get_chaos_middleware()
    middleware.enabled = bool(enabled)
    middleware.force_next_rate_limit = bool(enabled)
    return middleware


def is_chaos_mode_enabled() -> bool:
    """Return whether chaos mode is currently enabled."""
    settings = get_settings()
    return bool(settings.CHAOS_MODE)


def _tag_chaos_exception(error: Exception, injection_type: str) -> Exception:
    """Attach chaos metadata on exceptions for downstream trace tagging."""
    setattr(error, "chaos_injected", True)
    setattr(error, "injection_type", injection_type)
    return error
