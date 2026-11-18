"""Tools an agent can call by name from its ReAct loop.

A tool is offered to an agent only if the agent's role allows it AND it is usable on this
deployment (API key present, operator switch on). The model never sees a tool it cannot use.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Awaitable, Callable

from config import get_settings

from .api_caller import call_api as call_api_async
from .code_exec import execute_code
from .dispatch import failure_block, fence_untrusted
from .web_search import format_search_results, web_search


@dataclass(slots=True)
class Observation:
    """What a tool call returns to the agent."""

    ok: bool
    text: str
    error: str | None = None


@dataclass(slots=True, frozen=True)
class AgentTool:
    name: str
    description: str
    input_hint: str
    run: Callable[[str], Awaitable[Observation]]


async def _web_search(query: str) -> Observation:
    result = await web_search(query=query.strip()[:300], max_results=5)
    if not result.success:
        return Observation(False, failure_block("web_search", result.error_message), result.error_message)
    if not (result.data or {}).get("results"):
        reason = f"no results for '{query.strip()[:80]}'"
        return Observation(False, failure_block("web_search", reason), reason)
    return Observation(True, fence_untrusted("web_search", format_search_results(result)))


async def _http_get(url: str) -> Observation:
    result = await call_api_async(url=url.strip())
    if not result.success:
        return Observation(False, failure_block("http_get", result.error_message), result.error_message)
    body = json.dumps((result.data or {}).get("body"), ensure_ascii=True, default=str)
    return Observation(True, fence_untrusted("http_get", body))


async def _python(code: str) -> Observation:
    cleaned = code.strip().removeprefix("```python").removeprefix("```").removesuffix("```").strip()
    result = await execute_code(cleaned)
    if not result.success:
        return Observation(False, failure_block("python", result.error_message), result.error_message)
    return Observation(True, fence_untrusted("python", str((result.data or {}).get("stdout") or "")))


ALL_TOOLS: dict[str, AgentTool] = {
    "web_search": AgentTool("web_search", "Search the web for current facts. Returns titles, URLs and snippets.",
                            "a short search query", _web_search),
    "http_get": AgentTool("http_get", "Fetch a public http(s) URL and return its body.", "one URL", _http_get),
    "python": AgentTool("python", "Run Python 3 code and return what it prints. No files, no packages.",
                        "Python code that prints its result", _python),
}


def tool_is_usable(name: str) -> bool:
    """Whether the deployment can actually run this tool."""
    settings = get_settings()
    if name == "web_search":
        return bool(settings.tavily_api_key)
    if name == "http_get":
        return bool(settings.ENABLE_API_CALL)
    if name == "python":
        return bool(settings.ENABLE_CODE_EXEC)
    return False


def tools_for(names: list[str]) -> list[AgentTool]:
    """Usable tools among ``names``, in the given order."""
    return [ALL_TOOLS[name] for name in names if name in ALL_TOOLS and tool_is_usable(name)]
