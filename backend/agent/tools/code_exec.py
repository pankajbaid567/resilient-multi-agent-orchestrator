"""Python execution tool for agent steps.

Off by default (ENABLE_CODE_EXEC=false). Model-written code is untrusted: on a public
deployment it could read API keys or reach internal services. When an operator enables it,
the child process gets an empty environment, Python isolated mode, a throwaway working
directory, CPU/file/process limits and truncated output. This is a mitigation, not a
sandbox: the child can still open network connections. Use a real sandbox (gVisor,
Firecracker, E2B) before exposing it to untrusted users.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any

from config import get_settings

from .web_search import ToolResult

MAX_OUTPUT_CHARS = 8_000
DISABLED_MESSAGE = "code_exec is disabled on this deployment (ENABLE_CODE_EXEC=false)"


def code_exec_enabled() -> bool:
    """Return whether the operator has enabled model-written code execution."""
    return bool(get_settings().ENABLE_CODE_EXEC)


def _limit_resources(cpu_seconds: int) -> None:  # pragma: no cover - runs in the child process
    """Apply POSIX resource limits in the child before exec."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except Exception:
        pass


def _run_isolated(code: str, timeout_seconds: int) -> dict[str, Any]:
    """Run code in a restricted child process and return a normalized payload."""
    started = perf_counter()
    if not code_exec_enabled():
        return _result(False, "", "", None, DISABLED_MESSAGE, started)

    with tempfile.TemporaryDirectory(prefix="agent-exec-") as workdir:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, code passed as one argument
                [sys.executable, "-I", "-S", "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                preexec_fn=(lambda: _limit_resources(timeout_seconds)) if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired as exc:
            return _result(
                False,
                _as_text(exc.stdout),
                _as_text(exc.stderr),
                None,
                f"Execution timed out after {timeout_seconds} seconds",
                started,
            )
        except Exception as exc:  # pragma: no cover - OS-level spawn failure
            return _result(False, "", "", None, str(exc), started)

    success = completed.returncode == 0
    stderr = completed.stderr or ""
    error = "" if success else (stderr.strip() or f"Process exited with code {completed.returncode}")
    return _result(success, completed.stdout or "", stderr, completed.returncode, error, started)


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _result(
    success: bool,
    stdout: str,
    stderr: str,
    return_code: int | None,
    error_message: str,
    started: float,
) -> dict[str, Any]:
    return {
        "success": success,
        "data": {
            "stdout": stdout[:MAX_OUTPUT_CHARS],
            "stderr": stderr[:MAX_OUTPUT_CHARS],
            "return_code": return_code,
            "network_access": "not_blocked",
        },
        "error_message": error_message,
        "latency_ms": int((perf_counter() - started) * 1000),
        "tool_name": "code_exec",
    }


async def execute_code(code: str, language: str = "python", timeout: int = 10) -> ToolResult:
    """Execute model-written Python without blocking the event loop."""
    started = perf_counter()
    if str(language or "python").strip().lower() not in {"python", "py"}:
        return ToolResult(False, {}, f"Unsupported language: {language}", 0, "code_exec")
    payload = await asyncio.to_thread(_run_isolated, code, int(timeout))
    return ToolResult(
        success=bool(payload["success"]),
        data=payload["data"],
        error_message=str(payload["error_message"]),
        latency_ms=int((perf_counter() - started) * 1000),
        tool_name="code_exec",
    )


__all__ = ["code_exec_enabled", "execute_code"]
