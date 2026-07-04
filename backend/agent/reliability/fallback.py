"""LLM fallback-chain helpers for provider/model failover."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, List, Optional

from config import get_settings
from services.llm_service import LLMResponse, LLMResponseError, call_llm

from .chaos import get_chaos_middleware
from .retry import NON_RETRYABLE_EXCEPTIONS, RETRYABLE_EXCEPTIONS, retry_with_backoff

logger = logging.getLogger(__name__)

FALLBACK_CHAIN = [
    {
        "provider": "open_source",
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "label": "Llama 3.1 8B",
    },
    {
        "provider": "open_source",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "label": "Qwen 2.5 7B",
    },
    {
        "provider": "open_source",
        "model": "mistralai/Mistral-7B-Instruct-v0.3",
        "label": "Mistral 7B",
    },
]


class AllProvidersFailedError(Exception):
    """Raised when every provider in the fallback chain fails."""

    def __init__(self, errors: List[dict]):
        self.errors = errors
        providers = [entry["provider"] for entry in errors]
        super().__init__(f"All LLM providers failed: {providers}")


def get_fallback_chain() -> list[str]:
    """Return ordered model names for compatibility with existing callers."""
    return [entry["model"] for entry in FALLBACK_CHAIN]


async def call_with_fallback(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    json_mode: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 60,
    task_id: Optional[str] = None,
    step_id: Optional[str] = None,
    fallback_chain: Optional[List[dict]] = None,
    circuit_breaker: Optional["CircuitBreakerManager"] = None,
) -> LLMResponse:
    """
    Try each provider in the fallback chain until one succeeds.

    For each provider:
      1. Check circuit breaker — if OPEN, skip to next
      2. Call call_llm() wrapped with retry_with_backoff()
      3. On success: record success in circuit breaker, return result
      4. On failure: record failure in circuit breaker, log fallback, try next

    If ALL providers fail: raise AllProvidersFailedError with all errors

    Returns LLMResponse with actual model/provider used
    """
    chain = list(fallback_chain or FALLBACK_CHAIN)
    if not chain:
        raise AllProvidersFailedError(errors=[{"provider": "none", "error": "Fallback chain is empty"}])

    errors: list[dict[str, Any]] = []
    for index, entry in enumerate(chain):
        provider = str(entry.get("provider", "")).strip().lower()
        model = str(entry.get("model", "")).strip()
        label = str(entry.get("label", f"{provider}/{model}")).strip()
        provider_key = f"{provider}/{model}"
        context = _log_context(task_id=task_id, step_id=step_id)
        chaos_mode_enabled = bool(get_settings().CHAOS_MODE)
        chaos_injection: str | None = None

        if not provider or not model:
            invalid_reason = f"Invalid fallback entry at index {index}: {entry}"
            logger.warning("%s%s", context, invalid_reason)
            errors.append({"provider": provider_key or "invalid", "error": invalid_reason})
            await _log_fallback_transition(chain=chain, index=index, reason=invalid_reason)
            continue

        logger.info("%sTrying provider %s (%s)", context, provider_key, label)

        if circuit_breaker is not None:
            is_open = await _circuit_is_open(circuit_breaker, provider_key)
            if is_open:
                reason = f"Circuit open for provider {provider_key}"
                logger.warning("%sSkipping provider %s: %s", context, provider_key, reason)
                errors.append({"provider": provider_key, "error": reason})
                await _log_fallback_transition(chain=chain, index=index, reason=reason)
                continue

        try:
            if chaos_mode_enabled:
                chaos = get_chaos_middleware()
                chaos.enabled = True
                chaos_context = f"provider={provider}, step={step_id or 'unknown'}"
                chaos_injection = await chaos.maybe_inject(chaos_context)

            response = await retry_with_backoff(
                func=lambda provider=provider, model=model: call_llm(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    provider=provider,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    timeout=timeout,
                ),
                max_retries=1,
                retryable_exceptions=RETRYABLE_EXCEPTIONS,
                non_retryable_exceptions=_non_retryable_for_fallback(),
                task_id=task_id,
                step_id=step_id,
            )

            if chaos_injection == "CORRUPT":
                half = len(response.text) // 2
                response.text = response.text[:half]

            if circuit_breaker is not None:
                await _circuit_record_success(circuit_breaker, provider_key)

            if index > 0:
                logger.info("%sProvider fallback succeeded with %s", context, provider_key)
            else:
                logger.info("%sPrimary provider succeeded with %s", context, provider_key)

            return response
        except Exception as exc:
            if circuit_breaker is not None:
                await _circuit_record_failure(circuit_breaker, provider_key)

            reason = f"{exc.__class__.__name__}: {exc}"
            error_entry: dict[str, Any] = {"provider": provider_key, "error": reason}
            chaos_tag = _extract_chaos_tag(exc)
            if chaos_tag:
                error_entry["chaos_injected"] = True
                error_entry["injection_type"] = chaos_tag

            errors.append(error_entry)
            logger.warning("%sProvider attempt failed for %s. Reason: %s", context, provider_key, reason)
            await _log_fallback_transition(chain=chain, index=index, reason=reason)
            continue

    logger.error("%sAll providers exhausted. Escalating.", _log_context(task_id=task_id, step_id=step_id))
    raise AllProvidersFailedError(errors=errors)


def _non_retryable_for_fallback() -> tuple:
    """Return non-retryable exception types while preserving LLM empty-response retries."""
    return tuple(exc for exc in NON_RETRYABLE_EXCEPTIONS if exc is not LLMResponseError)


async def _log_fallback_transition(chain: List[dict], index: int, reason: str) -> None:
    """Emit structured fallback transition logs when moving across providers."""
    if index >= len(chain) - 1:
        return

    current = chain[index]
    nxt = chain[index + 1]
    payload = {
        "from_provider": f"{current.get('provider', '')}/{current.get('model', '')}",
        "to_provider": f"{nxt.get('provider', '')}/{nxt.get('model', '')}",
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.warning("llm_fallback_event=%s", payload)


def _log_context(task_id: Optional[str], step_id: Optional[str]) -> str:
    """Build standard log context prefix."""
    parts = []
    if task_id:
        parts.append(f"task_id={task_id}")
    if step_id:
        parts.append(f"step_id={step_id}")
    return (" ".join(parts) + " ") if parts else ""


async def _circuit_is_open(circuit_breaker: Any, provider: str) -> bool:
    """Check circuit breaker state with support for sync/async manager styles."""
    if hasattr(circuit_breaker, "is_open"):
        return bool(await _maybe_await(circuit_breaker.is_open(provider)))

    if hasattr(circuit_breaker, "allow_request"):
        # Basic CircuitBreaker class in this repository exposes allow_request() without provider.
        return not bool(await _maybe_await(circuit_breaker.allow_request()))

    return False


async def _circuit_record_success(circuit_breaker: Any, provider: str) -> None:
    """Record provider success against the circuit breaker if supported."""
    if hasattr(circuit_breaker, "record_success"):
        await _maybe_await(circuit_breaker.record_success(provider))
        return

    if hasattr(circuit_breaker, "record"):
        await _maybe_await(circuit_breaker.record(True))


async def _circuit_record_failure(circuit_breaker: Any, provider: str) -> None:
    """Record provider failure against the circuit breaker if supported."""
    if hasattr(circuit_breaker, "record_failure"):
        await _maybe_await(circuit_breaker.record_failure(provider))
        return

    if hasattr(circuit_breaker, "record"):
        await _maybe_await(circuit_breaker.record(False))


async def _maybe_await(value: Any) -> Any:
    """Await values when they are awaitable, otherwise return directly."""
    if hasattr(value, "__await__"):
        return await value
    return value


def _extract_chaos_tag(error: Exception) -> str | None:
    """Extract chaos injection metadata from raised exception objects."""
    if not bool(getattr(error, "chaos_injected", False)):
        return None

    injection_type = str(getattr(error, "injection_type", "")).strip().lower()
    return injection_type or "unknown"
