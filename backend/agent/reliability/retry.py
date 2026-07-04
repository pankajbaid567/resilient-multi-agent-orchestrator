"""Retry policy utilities implementing exponential backoff with jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, TypeVar

from services.llm_service import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    call_llm,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MaxRetriesExceededError(Exception):
    """Raised when retry attempts are exhausted without a successful result."""

    def __init__(self, attempts: int, last_error: Exception):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"Failed after {attempts} attempts. Last error: {last_error}")


RETRYABLE_EXCEPTIONS = (
    LLMTimeoutError,
    LLMRateLimitError,
    LLMConnectionError,
    LLMResponseError,
    asyncio.TimeoutError,
    ConnectionError,
    TimeoutError,
)

NON_RETRYABLE_EXCEPTIONS = (
    LLMResponseError,
    ValueError,
    KeyError,
    TypeError,
)


def compute_backoff_seconds(attempt: int, base_seconds: float = 1.0, max_seconds: float = 30.0) -> float:
    """Return capped exponential backoff delay with random jitter."""
    jitter = random.uniform(0, 1)
    return min(base_seconds * (2**attempt) + jitter, max_seconds)


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    non_retryable_exceptions: tuple = (),
    on_retry: Optional[Callable[[int, float, Exception], Awaitable[None]]] = None,
    task_id: Optional[str] = None,
    step_id: Optional[str] = None,
) -> T:
    """
    Retry an async function with exponential backoff + jitter.

    Backoff formula: delay = min(base_delay * 2^attempt + random(0, 1), max_delay)

    Args:
        func: Async callable to retry (takes no args — use functools.partial or lambda)
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Cap on delay (prevents absurd waits)
        retryable_exceptions: Exception types that trigger retry
        non_retryable_exceptions: Exception types that immediately propagate (override retryable)
        on_retry: Optional async callback on each retry (receives: attempt, delay, exception)
        task_id: For logging context
        step_id: For logging context

    Returns: The result of func() on success
    Raises: MaxRetriesExceededError if all attempts fail
    """
    if not callable(func):
        raise TypeError("func must be callable")
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if base_delay < 0:
        raise ValueError("base_delay must be >= 0")
    if max_delay <= 0:
        raise ValueError("max_delay must be > 0")

    retryable_types = retryable_exceptions if retryable_exceptions else (Exception,)
    non_retryable_types = non_retryable_exceptions if non_retryable_exceptions else ()

    errors: list[Exception] = []
    total_attempts = max_retries + 1
    step_label = step_id or "unknown_step"
    task_prefix = f"task_id={task_id} " if task_id else ""

    for attempt_index in range(total_attempts):
        try:
            result = await func()
            if attempt_index > 0:
                logger.info(
                    "Succeeded on attempt %s for %s",
                    attempt_index + 1,
                    step_label,
                )
            return result
        except Exception as error:  # pragma: no branch - classification below decides behavior
            errors.append(error)

            if non_retryable_types and isinstance(error, non_retryable_types):
                raise

            if not isinstance(error, retryable_types):
                raise

            if attempt_index >= max_retries:
                logger.error(
                    "All %s retries exhausted for %s. Escalating.",
                    max_retries,
                    step_label,
                )
                exhausted = MaxRetriesExceededError(attempts=total_attempts, last_error=error)
                exhausted.errors = errors
                raise exhausted from error

            retry_attempt = attempt_index + 1
            delay = compute_backoff_seconds(
                attempt=attempt_index,
                base_seconds=base_delay,
                max_seconds=max_delay,
            )
            logger.warning(
                "%sRetry %s/%s for %s after %.1fs. Error: %s",
                task_prefix,
                retry_attempt,
                max_retries,
                step_label,
                delay,
                error,
            )

            if on_retry is not None:
                try:
                    await on_retry(retry_attempt, delay, error)
                except Exception as callback_error:  # pragma: no cover - callback should not break retries
                    logger.warning(
                        "%sretry_on_retry_callback_failed for %s. Error: %s",
                        task_prefix,
                        step_label,
                        callback_error,
                    )

            await asyncio.sleep(delay)

    # Defensive fallback path; loop always returns or raises above.
    last_error = errors[-1] if errors else RuntimeError("retry_with_backoff failed without captured exception")
    exhausted = MaxRetriesExceededError(attempts=total_attempts, last_error=last_error)
    exhausted.errors = errors
    raise exhausted


async def retry_llm_call(prompt: str, **llm_kwargs) -> LLMResponse:
    """Convenience: retry an LLM call with sensible defaults."""
    return await retry_with_backoff(
        lambda: call_llm(prompt, **llm_kwargs),
        retryable_exceptions=RETRYABLE_EXCEPTIONS,
    )
