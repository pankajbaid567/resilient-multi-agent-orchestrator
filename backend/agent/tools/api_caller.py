"""Generic HTTP API caller tool powered by httpx."""

from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

from config import get_settings

from .url_guard import BlockedURLError, check_public_url
from .web_search import ToolResult

DISABLED_MESSAGE = "api_call is disabled on this deployment (ENABLE_API_CALL=false)"
MAX_BODY_CHARS = 8_000


def api_call_enabled() -> bool:
    """Return whether the operator has enabled outbound HTTP calls chosen by the model."""
    return bool(get_settings().ENABLE_API_CALL)


def _refusal(url: str, started: float) -> str | None:
    """Return a reason to refuse the request, or None when it may proceed."""
    if not api_call_enabled():
        return DISABLED_MESSAGE
    try:
        check_public_url(url)
    except BlockedURLError as exc:
        return f"Blocked URL: {exc}"
    return None


async def call_api(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    timeout: int = 30,
) -> ToolResult:
    """Generic HTTP API caller using httpx."""
    started = perf_counter()
    refusal = _refusal(str(url or ""), started)
    if refusal:
        return ToolResult(False, {}, refusal, _latency_ms(started), "api_caller")

    normalized_url = str(url or "").strip()
    if not normalized_url:
        return ToolResult(
            success=False,
            data={},
            error_message="URL is required",
            latency_ms=_latency_ms(started),
            tool_name="api_caller",
        )

    normalized_method = str(method or "GET").upper()
    request_headers = dict(headers or {})
    request_body = dict(body or {})

    try:
        client_timeout = httpx.Timeout(float(timeout))
        async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=False) as client:
            response = await client.request(
                method=normalized_method,
                url=normalized_url,
                headers=request_headers,
                json=request_body if request_body else None,
            )

        parsed_body = _parse_body(response)
        payload = {
            "url": str(response.request.url),
            "method": normalized_method,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": parsed_body,
        }

        if response.is_success:
            return ToolResult(
                success=True,
                data=payload,
                error_message="",
                latency_ms=_latency_ms(started),
                tool_name="api_caller",
            )

        return ToolResult(
            success=False,
            data=payload,
            error_message=f"HTTP {response.status_code}",
            latency_ms=_latency_ms(started),
            tool_name="api_caller",
        )
    except httpx.TimeoutException:
        return ToolResult(
            success=False,
            data={},
            error_message="API request timed out",
            latency_ms=_latency_ms(started),
            tool_name="api_caller",
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            data={},
            error_message=str(exc),
            latency_ms=_latency_ms(started),
            tool_name="api_caller",
        )


def call_api_sync(
    method: str,
    url: str,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Backward-compatible sync API used by current executor call-sites."""
    started = perf_counter()
    refusal = _refusal(str(url or ""), started)
    if refusal:
        return {"success": False, "data": {}, "error_message": refusal, "latency_ms": _latency_ms(started), "tool_name": "api_caller"}
    normalized_method = str(method or "GET").upper()
    normalized_url = str(url or "").strip()

    if not normalized_url:
        return {
            "success": False,
            "data": {},
            "error_message": "URL is required",
            "latency_ms": _latency_ms(started),
            "tool_name": "api_caller",
        }

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.request(
                method=normalized_method,
                url=normalized_url,
                headers=dict(headers or {}),
                json=dict(json_body or {}) if json_body else None,
            )

        payload = {
            "url": str(response.request.url),
            "method": normalized_method,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": _parse_body(response),
        }
        return {
            "success": response.is_success,
            "data": payload,
            "error_message": "" if response.is_success else f"HTTP {response.status_code}",
            "latency_ms": _latency_ms(started),
            "tool_name": "api_caller",
        }
    except httpx.TimeoutException:
        return {
            "success": False,
            "data": {},
            "error_message": "API request timed out",
            "latency_ms": _latency_ms(started),
            "tool_name": "api_caller",
        }
    except Exception as exc:
        return {
            "success": False,
            "data": {},
            "error_message": str(exc),
            "latency_ms": _latency_ms(started),
            "tool_name": "api_caller",
        }


def _parse_body(response: httpx.Response) -> Any:
    """Parse response body as JSON when possible, else return text."""
    try:
        return response.json()
    except ValueError:
        return response.text[:MAX_BODY_CHARS]


def _latency_ms(started: float) -> int:
    """Compute elapsed milliseconds from perf_counter start value."""
    return int((perf_counter() - started) * 1000)


__all__ = ["call_api", "call_api_sync"]
