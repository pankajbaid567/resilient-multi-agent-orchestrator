"""Unified LLM service layer for provider-specific model calls."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
import logging
from time import perf_counter
from typing import Any

from anthropic import AsyncAnthropic
from opentelemetry import trace
from openai import AsyncOpenAI

from config import get_settings
from services.usage import record_llm_call

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


class LLMServerError(LLMError):
    """Raised on a provider-side 5xx error (transient, worth retrying)."""


class LLMRequestError(LLMError):
    """Raised on a client error (400/401/403/404/422): retrying the same request cannot help."""


@dataclass(slots=True)
class LLMResponse:
    """Normalized response payload returned by the LLM service."""

    text: str
    tokens_used: int
    latency_ms: int
    model_used: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""

    @property
    def truncated(self) -> bool:
        """True when the provider stopped because of the token limit (the answer is cut off)."""
        return self.finish_reason in {"length", "max_tokens"}


_openai_client_created_at = 0.0
BEDROCK_KEY_REFRESH_SECONDS = 45 * 60  # short-term keys last up to 12 h; refresh well before


def _bedrock_role_key(region: str) -> str:
    """Short-term Bedrock API key signed with the machine's IAM role credentials."""
    from aws_bedrock_token_generator import provide_token

    return provide_token(region=region)


def get_openai_client() -> AsyncOpenAI:
    """Return a lazily initialized OpenAI-compatible async client.

    With BEDROCK_ROLE_KEY_REGION set, the API key is a short-term Bedrock key generated from
    the instance role and the client is rebuilt every BEDROCK_KEY_REFRESH_SECONDS.
    """
    global _openai_client, _openai_client_created_at
    settings = get_settings()
    role_region = (settings.BEDROCK_ROLE_KEY_REGION or "").strip()
    stale = role_region and (time.monotonic() - _openai_client_created_at) > BEDROCK_KEY_REFRESH_SECONDS
    if _openai_client is None or stale:
        if role_region:
            api_key = _bedrock_role_key(role_region)
            base_url = settings.open_source_base_url or f"https://bedrock-runtime.{role_region}.amazonaws.com/openai/v1"
            client_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
        else:
            api_key = (settings.open_source_api_key or settings.openai_api_key).strip()
            if not api_key:
                raise LLMConnectionError(
                    "No LLM API key configured. Set OPEN_SOURCE_API_KEY (recommended) or OPENAI_API_KEY."
                )
            client_kwargs = {"api_key": api_key}
            # OPEN_SOURCE_API_KEY implies OpenAI-compatible open-source endpoint usage.
            if settings.open_source_api_key and settings.open_source_base_url:
                client_kwargs["base_url"] = settings.open_source_base_url
        _openai_client = AsyncOpenAI(**client_kwargs)
        _openai_client_created_at = time.monotonic()
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
    model: str = "",
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
    model = model or get_settings().primary_model
    with trace.get_tracer("reliable-agent").start_as_current_span(
        f"chat {model}", kind=trace.SpanKind.CLIENT,
        attributes={"gen_ai.operation.name": "chat", "gen_ai.system": normalized_provider,
                    "gen_ai.request.model": model, "gen_ai.request.max_tokens": max_tokens,
                    "gen_ai.request.temperature": temperature},
    ) as span:
        response = await _call_llm_inner(prompt, system_prompt, model, normalized_provider, provider,
                                         temperature, max_tokens, json_mode, timeout, span)
    return response


async def _call_llm_inner(
    prompt: str,
    system_prompt: str,
    model: str,
    normalized_provider: str,
    provider: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    timeout: int,
    span: Any,
) -> LLMResponse:
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
            finish_reason = str(getattr((getattr(response, "choices", None) or [None])[0], "finish_reason", "") or "")
            text = strip_reasoning(_extract_openai_text(response), truncated=finish_reason == "length")
            tokens_used = _extract_openai_tokens(response)
            input_tokens, output_tokens = _usage_split(response, "prompt_tokens", "completion_tokens")
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
            input_tokens, output_tokens = _usage_split(response, "input_tokens", "output_tokens")
            finish_reason = str(getattr(response, "stop_reason", "") or "")
            model_used = str(getattr(response, "model", model) or model)

        if not text.strip():
            raise LLMResponseError("LLM returned an empty response body")

        latency_ms = int((perf_counter() - started) * 1000)
        record_llm_call(model_used, normalized_provider, input_tokens, output_tokens, latency_ms, ok=True)
        span.set_attributes({"gen_ai.response.model": model_used, "gen_ai.usage.input_tokens": input_tokens,
                             "gen_ai.usage.output_tokens": output_tokens})
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
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )
    except LLMError as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        record_llm_call(model, normalized_provider, 0, 0, latency_ms, ok=False, error=type(exc).__name__)
        span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
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
        record_llm_call(model, normalized_provider, 0, 0, latency_ms, ok=False, error=type(mapped_error).__name__)
        span.set_status(trace.Status(trace.StatusCode.ERROR, type(mapped_error).__name__))
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
        "max_tokens": max_tokens,
    }
    settings = get_settings()
    if settings.LLM_SEND_TEMPERATURE:
        request_kwargs["temperature"] = temperature
    if settings.LLM_REASONING_EFFORT:
        request_kwargs["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    if json_mode:
        request_kwargs["response_format"] = {"type": "json_object"}

    try:
        return await asyncio.wait_for(client.chat.completions.create(**request_kwargs), timeout=timeout)
    except Exception as exc:
        # Some OpenAI-compatible endpoints reject ``response_format``. Only that rejection
        # (a 400/422 from the provider) is worth one retry without it; timeouts, rate limits
        # and server errors propagate so the retry layer can classify them.
        if json_mode and "response_format" in request_kwargs and _extract_status_code(exc) in {400, 422}:
            logger.warning(
                "json_mode_fallback model=%s error=%s — retrying without response_format",
                model,
                exc,
            )
            request_kwargs.pop("response_format", None)
            request_kwargs["messages"] = [
                {"role": "system", "content": f"{system_prompt}\nRespond ONLY with valid JSON."},
                {"role": "user", "content": prompt},
            ]
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


_REASONING_BLOCK = re.compile(r"<(reasoning|think|thinking)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_REASONING = re.compile(r"^\s*<(reasoning|think|thinking)>.*", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str, truncated: bool = False) -> str:
    """Remove inline chain-of-thought blocks some models put in the answer text.

    Reasoning models served through OpenAI-compatible endpoints (for example gpt-oss on
    Bedrock) return "<reasoning>...</reasoning>" before the answer; Qwen uses "<think>". Left
    in, it corrupts answers and JSON parsing. An unclosed block is dropped only when the
    response was cut off by the token limit (then no answer was produced); otherwise the text
    is kept, because some replies omit the closing tag and put the answer after it.
    """
    cleaned = _REASONING_BLOCK.sub("", text or "")
    if truncated:
        cleaned = _UNCLOSED_REASONING.sub("", cleaned)
    else:
        cleaned = re.sub(r"^\s*<(reasoning|think|thinking)>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


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


def _usage_split(response: Any, input_field: str, output_field: str) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) as reported by the provider, or zeros."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    try:
        return int(getattr(usage, input_field, 0) or 0), int(getattr(usage, output_field, 0) or 0)
    except (TypeError, ValueError):
        return 0, 0


def _map_provider_error(exc: Exception, provider: str, model: str) -> LLMError:
    """Map provider and transport exceptions to typed LLM service errors."""
    if isinstance(exc, asyncio.TimeoutError) or exc.__class__.__name__ == "APITimeoutError":
        return LLMTimeoutError(f"LLM call timed out for provider={provider} model={model}")

    status_code = _extract_status_code(exc)
    if status_code == 429 or exc.__class__.__name__ == "RateLimitError":
        return LLMRateLimitError(f"LLM rate limit hit for provider={provider} model={model}")

    if status_code is not None and status_code >= 500:
        return LLMServerError(f"LLM provider error {status_code} for provider={provider} model={model}")

    if status_code is not None and 400 <= status_code < 500:
        return LLMRequestError(f"LLM request rejected ({status_code}) for provider={provider} model={model}: {exc}")

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
