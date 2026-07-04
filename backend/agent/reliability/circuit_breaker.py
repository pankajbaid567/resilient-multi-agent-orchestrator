"""Circuit breaker implementation for provider-level reliability control."""

from __future__ import annotations

import asyncio
import logging
from time import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker for a single provider."""

    def __init__(
        self,
        provider_name: str,
        failure_threshold: float = 0.5,
        min_calls: int = 3,
        window_seconds: int = 60,
        cooldown_seconds: int = 120,
    ):
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.min_calls = min_calls
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds

        self.state = "closed"
        self.calls: List[dict] = []
        self.opened_at: Optional[float] = None

        self._lock = asyncio.Lock()
        self._half_open_probe_in_flight = False

    async def is_open(self) -> bool:
        """Check if circuit is open (calls should be skipped)."""
        async with self._lock:
            self._cleanup_old_calls()
            now = time()

            if self.state == "open":
                if self.opened_at is not None and (now - self.opened_at) >= self.cooldown_seconds:
                    self.state = "half_open"
                    self._half_open_probe_in_flight = False
                    logger.info("circuit_transition provider=%s from=open to=half_open", self.provider_name)
                else:
                    return True

            if self.state == "half_open":
                if self._half_open_probe_in_flight:
                    return True
                self._half_open_probe_in_flight = True
                return False

            return False

    async def record_success(self):
        """Record a successful call. If half_open -> transition to closed."""
        async with self._lock:
            now = time()
            self.calls.append({"timestamp": now, "success": True})
            self._cleanup_old_calls(now=now)

            if self.state == "half_open":
                self.state = "closed"
                self.opened_at = None
                self._half_open_probe_in_flight = False
                logger.info("circuit_transition provider=%s from=half_open to=closed", self.provider_name)
            elif self.state == "open":
                # Defensive normalization in case caller records success unexpectedly in OPEN state.
                self.state = "closed"
                self.opened_at = None
                self._half_open_probe_in_flight = False
                logger.info("circuit_transition provider=%s from=open to=closed", self.provider_name)

    async def record_failure(self):
        """Record a failed call. Check if failure rate exceeds threshold."""
        async with self._lock:
            now = time()
            self.calls.append({"timestamp": now, "success": False})
            self._cleanup_old_calls(now=now)

            if self.state == "half_open":
                self.state = "open"
                self.opened_at = now
                self._half_open_probe_in_flight = False
                logger.warning("circuit_transition provider=%s from=half_open to=open", self.provider_name)
                return

            failure_rate = self._calculate_failure_rate()
            if self.state == "closed" and len(self.calls) >= self.min_calls and failure_rate > self.failure_threshold:
                self.state = "open"
                self.opened_at = now
                self._half_open_probe_in_flight = False
                logger.warning(
                    "circuit_transition provider=%s from=closed to=open failure_rate=%.3f calls=%s",
                    self.provider_name,
                    failure_rate,
                    len(self.calls),
                )

    async def get_state(self) -> dict:
        """Return current state for debugging/UI display."""
        async with self._lock:
            self._cleanup_old_calls()
            failure_rate = self._calculate_failure_rate()
            now = time()
            cooldown_remaining = 0.0
            if self.state == "open" and self.opened_at is not None:
                cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self.opened_at))

            return {
                "provider": self.provider_name,
                "state": self.state,
                "failure_rate": failure_rate,
                "calls_in_window": len(self.calls),
                "failure_threshold": self.failure_threshold,
                "min_calls": self.min_calls,
                "window_seconds": self.window_seconds,
                "cooldown_seconds": self.cooldown_seconds,
                "cooldown_remaining_seconds": round(cooldown_remaining, 3),
                "opened_at": self.opened_at,
            }

    async def allow_request(self) -> bool:
        """Return whether a call should be allowed for the provider."""
        return not await self.is_open()

    async def record(self, success: bool) -> None:
        """Compatibility helper that routes to record_success/record_failure."""
        if success:
            await self.record_success()
        else:
            await self.record_failure()

    def _cleanup_old_calls(self, now: Optional[float] = None):
        """Remove calls outside the sliding window."""
        current_time = now if now is not None else time()
        cutoff = current_time - self.window_seconds
        self.calls = [call for call in self.calls if float(call.get("timestamp", 0.0)) >= cutoff]

    def _calculate_failure_rate(self) -> float:
        """Calculate failure rate in current window."""
        if not self.calls:
            return 0.0
        failures = sum(1 for call in self.calls if not bool(call.get("success", False)))
        return failures / len(self.calls)


class CircuitBreakerManager:
    """Manages circuit breakers for all providers."""

    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get_breaker(self, provider: str) -> CircuitBreaker:
        """Get or create circuit breaker for a provider."""
        normalized_provider = provider.strip().lower()
        async with self._lock:
            breaker = self.breakers.get(normalized_provider)
            if breaker is None:
                breaker = CircuitBreaker(provider_name=normalized_provider)
                self.breakers[normalized_provider] = breaker
            return breaker

    async def is_open(self, provider: str) -> bool:
        """Return whether the provider circuit is currently open."""
        breaker = await self.get_breaker(provider)
        return await breaker.is_open()

    async def record_success(self, provider: str):
        """Record a successful provider call."""
        breaker = await self.get_breaker(provider)
        await breaker.record_success()

    async def record_failure(self, provider: str):
        """Record a failed provider call."""
        breaker = await self.get_breaker(provider)
        await breaker.record_failure()

    async def get_all_states(self) -> Dict[str, dict]:
        """Return all circuit breaker states (for UI/API)."""
        async with self._lock:
            items = list(self.breakers.items())

        states: Dict[str, dict] = {}
        for provider, breaker in items:
            states[provider] = await breaker.get_state()
        return states


_manager: CircuitBreakerManager | None = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Return a singleton circuit breaker manager instance."""
    global _manager
    if _manager is None:
        _manager = CircuitBreakerManager()
    return _manager
