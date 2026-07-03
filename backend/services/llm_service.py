"""Unified LLM service layer for provider-specific model calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from time import perf_counter
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from config import get_settings

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None
_anthropic_client: AsyncAnthropic | None = None


class LLMError(Exception):
    """Base exception type for all LLM service failures."""


class LLMTimeoutError(LLMError):
    """Raised when the provider call exceeds the configured timeout."""


class LLMRateLimitError(LLMError):
    """Raised when the provider responds with a rate-limit error."""


class LLMConnectionError(LLMError):
    """Raised when provider connectivity fails."""


class LLMResponseError(LLMError):
    """Raised when the provider returns an empty or invalid response payload."""


@dataclass(slots=True)
class LLMResponse:
    """Normalized response payload returned by the LLM service."""

    text: str
    tokens_used: int
    latency_ms: int
    model_used: str
    provider: str


def get_openai_client() -> AsyncOpenAI:
    """Return a lazily initialized singleton OpenAI async client."""
    global _openai_client
    if _openai_client is None:
        settings = get_settings()
        api_key = (settings.open_source_api_key or settings.openai_api_key).strip()
        if not api_key:
            raise LLMConnectionError(
                "No LLM API key configured. Set OPEN_SOURCE_API_KEY (recommended) or OPENAI_API_KEY."
            )

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        # OPEN_SOURCE_API_KEY implies OpenAI-compatible open-source endpoint usage.
        if settings.open_source_api_key and settings.open_source_base_url:
            client_kwargs["base_url"] = settings.open_source_base_url

        _openai_client = AsyncOpenAI(**client_kwargs)
    return _openai_client


def get_anthropic_client() -> AsyncAnthropic:
    """Return a lazily initialized singleton Anthropic async client."""
    global _anthropic_client
    if _anthropic_client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise LLMConnectionError("ANTHROPIC_API_KEY is not configured")
        _anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


async def call_llm(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    model: str = "meta-llama/Llama-3.1-8B-Instruct",
    provider: str = "open_source",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_mode: bool = False,
    timeout: int = 60,
) -> LLMResponse:
    """Make one LLM call to the selected provider and return a normalized response.

    For OpenAI calls, this uses chat completions via AsyncOpenAI.
    For Anthropic calls, this uses messages.create via AsyncAnthropic.
    """
    normalized_provider = provider.strip().lower()
    started = perf_counter()

    if normalized_provider not in {"openai", "anthropic", "open_source"}:
        raise LLMResponseError(
            f"Unsupported provider '{provider}'. Expected 'open_source', 'openai', or 'anthropic'."
        )

    try:
        if normalized_provider in {"openai", "open_source"}:
            response = await _call_openai(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                timeout=timeout,
            )
            text = _extract_openai_text(response)
            tokens_used = _extract_openai_tokens(response)
            model_used = str(getattr(response, "model", model) or model)
        else:
            response = await _call_anthropic(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                timeout=timeout,
            )
            text = _extract_anthropic_text(response)
            tokens_used = _extract_anthropic_tokens(response)
            model_used = str(getattr(response, "model", model) or model)

        if not text.strip():
            raise LLMResponseError("LLM returned an empty response body")

        latency_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "llm_call_success provider=%s model=%s tokens=%s latency_ms=%s",
            normalized_provider,
            model_used,
            tokens_used,
            latency_ms,
        )
        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            model_used=model_used,
            provider=normalized_provider,
        )
    except LLMError:
        latency_ms = int((perf_counter() - started) * 1000)
        logger.warning(
            "llm_call_failed provider=%s model=%s tokens=%s latency_ms=%s",
            normalized_provider,
            model,
            0,
            latency_ms,
        )
        raise
    except Exception as exc:
        mapped_error = _map_provider_error(exc, provider=normalized_provider, model=model)
        latency_ms = int((perf_counter() - started) * 1000)
        logger.warning(
            "llm_call_failed provider=%s model=%s tokens=%s latency_ms=%s error=%s",
            normalized_provider,
            model,
            0,
            latency_ms,
            mapped_error,
        )
        raise mapped_error from exc


async def _call_openai(
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    timeout: int,
) -> Any:
    """Execute one OpenAI chat completion request with timeout control."""
    client = get_openai_client()
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        request_kwargs["response_format"] = {"type": "json_object"}

    try:
        return await asyncio.wait_for(client.chat.completions.create(**request_kwargs), timeout=timeout)
    except Exception as exc:
        # Some open-source models served via OpenAI-compatible endpoints do not
        # support the ``response_format`` parameter.  When json_mode was requested
        # and the call fails, retry without structured-output enforcement and
        # instead append a JSON instruction to the system prompt.
        if json_mode and "response_format" in request_kwargs:
            logger.warning(
                "json_mode_fallback model=%s error=%s — retrying without response_format",
                model,
                exc,
            )
            request_kwargs.pop("response_format", None)
            request_kwargs["messages"][0]["content"] += "\nRespond ONLY with valid JSON."
            return await asyncio.wait_for(
                client.chat.completions.create(**request_kwargs), timeout=timeout
            )
        raise


async def _call_anthropic(
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    timeout: int,
) -> Any:
    """Execute one Anthropic messages request with timeout control."""
    client = get_anthropic_client()
    effective_system_prompt = system_prompt
    if json_mode:
        suffix = "Respond ONLY with valid JSON."
        if effective_system_prompt.strip():
            effective_system_prompt = f"{effective_system_prompt.rstrip()}\n{suffix}"
        else:
            effective_system_prompt = suffix

    return await asyncio.wait_for(
        client.messages.create(
            model=model,
            system=effective_system_prompt,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        ),
        timeout=timeout,
    )


def _extract_openai_text(response: Any) -> str:
    """Extract assistant response text from an OpenAI chat completion object."""
    choices = getattr(response, "choices", None)
    if not choices:
        raise LLMResponseError("OpenAI response did not include choices")

    message = getattr(choices[0], "message", None)
    if message is None:
        raise LLMResponseError("OpenAI response choice did not include a message")

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "".join(parts)
    return ""


def _extract_openai_tokens(response: Any) -> int:
    """Extract total token usage from an OpenAI response object."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0

    total_tokens = getattr(usage, "total_tokens", None)
    if isinstance(total_tokens, int):
        return total_tokens

    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    return int(prompt_tokens + completion_tokens)


def _extract_anthropic_text(response: Any) -> str:
    """Extract assistant response text from an Anthropic message object."""
    content = getattr(response, "content", None)
    if not content:
        raise LLMResponseError("Anthropic response did not include content")

    parts: list[str] = []
    for block in content:
        if hasattr(block, "text") and isinstance(block.text, str):
            parts.append(block.text)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _extract_anthropic_tokens(response: Any) -> int:
    """Extract total token usage from an Anthropic response object."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return int(input_tokens + output_tokens)


def _map_provider_error(exc: Exception, provider: str, model: str) -> LLMError:
    """Map provider and transport exceptions to typed LLM service errors."""
    if isinstance(exc, asyncio.TimeoutError) or exc.__class__.__name__ == "APITimeoutError":
        return LLMTimeoutError(f"LLM call timed out for provider={provider} model={model}")

    status_code = _extract_status_code(exc)
    if status_code == 429 or exc.__class__.__name__ == "RateLimitError":
        return LLMRateLimitError(f"LLM rate limit hit for provider={provider} model={model}")

    if isinstance(exc, OSError) or exc.__class__.__name__ in {
        "APIConnectionError",
        "APIConnectionException",
    }:
        return LLMConnectionError(f"LLM connection failure for provider={provider} model={model}")

    return LLMResponseError(f"Invalid LLM response for provider={provider} model={model}: {exc}")


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status-code extraction from provider exceptions."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    response = getattr(exc, "response", None)
    if response is not None:
        nested_status = getattr(response, "status_code", None)
        if isinstance(nested_status, int):
            return nested_status

    return None
