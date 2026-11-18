"""Reliability utilities: retries, model fallback, circuit breaking and chaos injection."""

from .chaos import ChaosMiddleware, get_chaos_middleware, is_chaos_mode_enabled, set_chaos_mode
from .circuit_breaker import CircuitBreaker, CircuitBreakerManager, get_circuit_breaker_manager
from .fallback import AllProvidersFailedError, call_with_fallback, default_chain, get_fallback_chain, provider_for_model
from .retry import (
	NON_RETRYABLE_EXCEPTIONS,
	RETRYABLE_EXCEPTIONS,
	MaxRetriesExceededError,
	compute_backoff_seconds,
	retry_llm_call,
	retry_with_backoff,
)

__all__ = [
	"CircuitBreaker",
	"CircuitBreakerManager",
	"AllProvidersFailedError",
	"ChaosMiddleware",
	"default_chain",
	"provider_for_model",
	"MaxRetriesExceededError",
	"NON_RETRYABLE_EXCEPTIONS",
	"RETRYABLE_EXCEPTIONS",
	"call_with_fallback",
	"compute_backoff_seconds",
	"get_chaos_middleware",
	"get_circuit_breaker_manager",
	"get_fallback_chain",
	"is_chaos_mode_enabled",
	"retry_llm_call",
	"retry_with_backoff",
	"set_chaos_mode",
]
