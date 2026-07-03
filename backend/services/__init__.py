"""Service-layer exports for LLM, Redis, vector memory, and tracing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .redis_service import RedisService, close_redis_client, get_redis_client, get_redis_service

if TYPE_CHECKING:
	from .llm_service import LLMResponse


async def call_llm(
	prompt: str,
	system_prompt: str = "You are a helpful AI assistant.",
	model: str = "meta-llama/Llama-3.1-8B-Instruct",
	provider: str = "open_source",
	temperature: float = 0.7,
	max_tokens: int = 4096,
	json_mode: bool = False,
	timeout: int = 60,
) -> "LLMResponse":
	"""Lazily import and invoke LLM service to avoid hard dependency at package import."""
	from .llm_service import call_llm as _call_llm

	return await _call_llm(
		prompt=prompt,
		system_prompt=system_prompt,
		model=model,
		provider=provider,
		temperature=temperature,
		max_tokens=max_tokens,
		json_mode=json_mode,
		timeout=timeout,
	)


async def append_trace_event(event: Any) -> None:
	"""Lazily import and invoke trace service to avoid hard dependency at package import."""
	from .trace_service import append_trace_event as _append_trace_event

	await _append_trace_event(event)


def __getattr__(name: str) -> Any:
	"""Provide lazy access to optional heavy services."""
	if name == "VectorService":
		from .vector_service import VectorService

		return VectorService
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
	"RedisService",
	"append_trace_event",
	"call_llm",
	"close_redis_client",
	"get_redis_client",
	"get_redis_service",
	"VectorService",
]
