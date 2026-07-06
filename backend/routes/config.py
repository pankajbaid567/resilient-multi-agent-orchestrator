"""Runtime configuration routes for frontend dashboard controls."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent.reliability.chaos import get_chaos_middleware, set_chaos_mode as set_chaos_runtime_mode
from config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

# Runtime-only provider chain used by frontend settings panel.
_runtime_provider_chain: list[dict[str, str]] = [
    {"provider": "open_source", "model": "meta-llama/Llama-3.1-8B-Instruct", "label": "Primary (Llama 3.1)"},
    {"provider": "open_source", "model": "Qwen/Qwen2.5-7B-Instruct", "label": "Fallback (Qwen 2.5)"},
    {"provider": "open_source", "model": "mistralai/Mistral-7B-Instruct-v0.3", "label": "Fallback (Mistral 7B)"},
]


class RuntimeConfigResponse(BaseModel):
    """Unified runtime configuration payload used by frontend settings."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class RuntimeConfigPatchRequest(BaseModel):
    """Partial config patch request body for /config endpoint."""

    chaos_mode: bool | None = None
    parallel_mode: bool | None = None
    multi_agent_mode: bool | None = None
    max_retries: int | None = Field(default=None, ge=0, le=20)
    step_timeout: int | None = Field(default=None, ge=5, le=600)
    max_concurrent_steps: int | None = Field(default=None, ge=1, le=32)
    providers: list[dict[str, Any]] | None = None


class EnabledRequest(BaseModel):
    """Request body for boolean feature toggles."""

    enabled: bool


class NumericValueRequest(BaseModel):
    """Request body for numeric config updates."""

    value: int


class ProvidersRequest(BaseModel):
    """Request body for provider fallback chain updates."""

    providers: list[dict[str, Any]]


@router.get("/config", response_model=RuntimeConfigResponse)
async def get_runtime_config() -> RuntimeConfigResponse:
    """Return current runtime settings consumed by frontend settings view."""
    try:
        settings = get_settings()
        return RuntimeConfigResponse(success=True, data=_serialize_runtime_config(settings), error=None)
    except Exception as exc:
        logger.exception("get_runtime_config_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config", response_model=RuntimeConfigResponse)
async def patch_runtime_config(payload: RuntimeConfigPatchRequest) -> RuntimeConfigResponse:
    """Patch one or more runtime settings in memory for subsequent task runs."""
    try:
        settings = get_settings()
        _apply_patch_to_settings(payload.model_dump(exclude_none=True), settings)
        return RuntimeConfigResponse(success=True, data=_serialize_runtime_config(settings), error=None)
    except Exception as exc:
        logger.exception("patch_runtime_config_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.get("/config/chaos-mode", response_model=RuntimeConfigResponse)
async def get_chaos_mode() -> RuntimeConfigResponse:
    """Compatibility endpoint returning only chaos_mode in data payload."""
    try:
        settings = get_settings()
        return RuntimeConfigResponse(success=True, data={"chaos_mode": bool(settings.CHAOS_MODE)}, error=None)
    except Exception as exc:
        logger.exception("get_chaos_mode_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/chaos-mode", response_model=RuntimeConfigResponse)
async def set_chaos_mode(payload: EnabledRequest) -> RuntimeConfigResponse:
    """Compatibility endpoint for chaos mode updates from frontend fallback."""
    try:
        settings = get_settings()
        settings.CHAOS_MODE = bool(payload.enabled)
        set_chaos_runtime_mode(bool(payload.enabled))
        return RuntimeConfigResponse(success=True, data={"chaos_mode": bool(settings.CHAOS_MODE)}, error=None)
    except Exception as exc:
        logger.exception("set_chaos_mode_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/parallel", response_model=RuntimeConfigResponse)
async def set_parallel_mode(payload: EnabledRequest) -> RuntimeConfigResponse:
    """Enable or disable parallel execution mode at runtime."""
    try:
        settings = get_settings()
        settings.PARALLEL_MODE = bool(payload.enabled)
        return RuntimeConfigResponse(success=True, data={"parallel_mode": bool(settings.PARALLEL_MODE)}, error=None)
    except Exception as exc:
        logger.exception("set_parallel_mode_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/agents", response_model=RuntimeConfigResponse)
async def set_multi_agent_mode(payload: EnabledRequest) -> RuntimeConfigResponse:
    """Enable or disable specialized multi-agent routing at runtime."""
    try:
        settings = get_settings()
        settings.MULTI_AGENT_MODE = bool(payload.enabled)
        return RuntimeConfigResponse(success=True, data={"multi_agent_mode": bool(settings.MULTI_AGENT_MODE)}, error=None)
    except Exception as exc:
        logger.exception("set_multi_agent_mode_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/retries", response_model=RuntimeConfigResponse)
async def set_max_retries(payload: NumericValueRequest) -> RuntimeConfigResponse:
    """Update maximum retry count used by executor/reliability flow."""
    try:
        settings = get_settings()
        settings.MAX_RETRIES = max(0, min(int(payload.value), 20))
        return RuntimeConfigResponse(success=True, data={"max_retries": int(settings.MAX_RETRIES)}, error=None)
    except Exception as exc:
        logger.exception("set_max_retries_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/timeout", response_model=RuntimeConfigResponse)
async def set_step_timeout(payload: NumericValueRequest) -> RuntimeConfigResponse:
    """Update per-step timeout used by executor/reliability flow."""
    try:
        settings = get_settings()
        settings.STEP_TIMEOUT = max(5, min(int(payload.value), 600))
        return RuntimeConfigResponse(success=True, data={"step_timeout": int(settings.STEP_TIMEOUT)}, error=None)
    except Exception as exc:
        logger.exception("set_step_timeout_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


@router.post("/config/providers", response_model=RuntimeConfigResponse)
async def set_provider_chain(payload: ProvidersRequest) -> RuntimeConfigResponse:
    """Update frontend-visible provider fallback chain (runtime metadata only)."""
    try:
        global _runtime_provider_chain
        _runtime_provider_chain = _normalize_provider_chain(payload.providers)
        return RuntimeConfigResponse(success=True, data={"providers": _runtime_provider_chain}, error=None)
    except Exception as exc:
        logger.exception("set_provider_chain_failed error=%s", exc)
        return _error_response(status_code=500, message=str(exc))


def _apply_patch_to_settings(patch: dict[str, Any], settings: Any) -> None:
    """Apply partial config patch to in-memory runtime settings."""
    if "chaos_mode" in patch:
        settings.CHAOS_MODE = bool(patch["chaos_mode"])
        set_chaos_runtime_mode(bool(patch["chaos_mode"]))

    if "parallel_mode" in patch:
        settings.PARALLEL_MODE = bool(patch["parallel_mode"])

    if "multi_agent_mode" in patch:
        settings.MULTI_AGENT_MODE = bool(patch["multi_agent_mode"])

    if "max_retries" in patch and patch["max_retries"] is not None:
        settings.MAX_RETRIES = max(0, min(int(patch["max_retries"]), 20))

    if "step_timeout" in patch and patch["step_timeout"] is not None:
        settings.STEP_TIMEOUT = max(5, min(int(patch["step_timeout"]), 600))

    if "max_concurrent_steps" in patch and patch["max_concurrent_steps"] is not None:
        settings.MAX_CONCURRENT_STEPS = max(1, min(int(patch["max_concurrent_steps"]), 32))

    if "providers" in patch and patch["providers"] is not None:
        global _runtime_provider_chain
        _runtime_provider_chain = _normalize_provider_chain(patch["providers"])


def _serialize_runtime_config(settings: Any) -> dict[str, Any]:
    """Normalize in-memory settings to frontend-facing config payload."""
    return {
        "chaos_mode": bool(getattr(settings, "CHAOS_MODE", False)),
        "parallel_mode": bool(getattr(settings, "PARALLEL_MODE", False)),
        "multi_agent_mode": bool(getattr(settings, "MULTI_AGENT_MODE", True)),
        "max_retries": int(getattr(settings, "MAX_RETRIES", 3)),
        "step_timeout": int(getattr(settings, "STEP_TIMEOUT", 60)),
        "max_concurrent_steps": int(getattr(settings, "MAX_CONCURRENT_STEPS", 5)),
        "providers": list(_runtime_provider_chain),
        "chaos_stats": {
            str(key): int(value)
            for key, value in get_chaos_middleware().get_stats().items()
        },
    }


def _normalize_provider_chain(raw_chain: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Sanitize provider chain entries into a compact stable structure."""
    normalized: list[dict[str, str]] = []

    for index, item in enumerate(raw_chain or []):
        if not isinstance(item, dict):
            continue

        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        label = str(item.get("label") or f"Fallback {index + 1}").strip()

        if not provider or not model:
            continue

        normalized.append(
            {
                "provider": provider,
                "model": model,
                "label": label,
            }
        )

    return normalized or list(_runtime_provider_chain)


def _error_response(status_code: int, message: str) -> JSONResponse:
    """Create standardized error envelope with explicit HTTP status."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": message,
        },
    )
