"""Sandboxed subprocess code execution utilities for agent tool calls."""

from __future__ import annotations

import asyncio
import subprocess
from time import perf_counter
from typing import Any

from .web_search import ToolResult


async def execute_code(
    code: str,
    language: str = "python",
    timeout: int = 10,
) -> ToolResult:
    """Execute code in a sandboxed subprocess."""
    started = perf_counter()
    normalized_language = str(language or "python").strip().lower()

    if normalized_language not in {"python", "py"}:
        return ToolResult(
            success=False,
            data={
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "language": normalized_language,
                "network_access": "not_enforced",
            },
            error_message=f"Unsupported language: {language}",
            latency_ms=_latency_ms(started),
            tool_name="code_exec",
        )

    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=int(timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            success=False,
            data={
                "exit_code": None,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "language": "python",
                "network_access": "not_enforced",
            },
            error_message=f"Execution timed out after {timeout} seconds",
            latency_ms=_latency_ms(started),
            tool_name="code_exec",
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            data={
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "language": "python",
                "network_access": "not_enforced",
            },
            error_message=str(exc),
            latency_ms=_latency_ms(started),
            tool_name="code_exec",
        )

    payload = {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "language": "python",
        "network_access": "not_enforced",
    }

    success = completed.returncode == 0
    error_message = ""
    if not success:
        stderr = (completed.stderr or "").strip()
        error_message = stderr if stderr else f"Process exited with code {completed.returncode}"

    return ToolResult(
        success=success,
        data=payload,
        error_message=error_message,
        latency_ms=_latency_ms(started),
        tool_name="code_exec",
    )


def execute_python_code(code: str, timeout_seconds: int = 10) -> dict[str, Any]:
    """Backward-compatible sync wrapper used by existing executor code paths."""
    started = perf_counter()
    try:
        completed = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "data": {
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "return_code": None,
                "network_access": "not_enforced",
            },
            "error_message": f"Execution timed out after {timeout_seconds} seconds",
            "latency_ms": _latency_ms(started),
            "tool_name": "code_exec",
        }
    except Exception as exc:
        return {
            "success": False,
            "data": {
                "stdout": "",
                "stderr": "",
                "return_code": None,
                "network_access": "not_enforced",
            },
            "error_message": str(exc),
            "latency_ms": _latency_ms(started),
            "tool_name": "code_exec",
        }

    return {
        "success": completed.returncode == 0,
        "data": {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "return_code": completed.returncode,
            "network_access": "not_enforced",
        },
        "error_message": ""
        if completed.returncode == 0
        else ((completed.stderr or "").strip() or f"Process exited with code {completed.returncode}"),
        "latency_ms": _latency_ms(started),
        "tool_name": "code_exec",
    }


def _latency_ms(started: float) -> int:
    """Compute elapsed milliseconds from perf_counter start value."""
    return int((perf_counter() - started) * 1000)


__all__ = ["execute_code", "execute_python_code"]
