"""Tavily-powered web search tool for agent context gathering."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from time import perf_counter
from typing import Any, List

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_SEARCH_TIMEOUT_SECONDS = 15

FILLER_PATTERNS = [
    r"\bsearch\s+for\b",
    r"\bfind\s+information\s+about\b",
    r"\bfind\s+details\s+about\b",
    r"\blook\s+up\b",
    r"\bresearch\b",
]

STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "how",
    "i",
    "in",
    "information",
    "into",
    "is",
    "it",
    "me",
    "of",
    "on",
    "please",
    "search",
    "show",
    "tell",
    "the",
    "to",
    "up",
    "what",
    "with",
}


@dataclass(slots=True)
class ToolResult:
    """Standardized tool response payload used by all tool integrations."""

    success: bool
    data: Any
    error_message: str
    latency_ms: int
    tool_name: str


async def web_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: List[str] = [],
    exclude_domains: List[str] = [],
) -> ToolResult:
    """
    Search the web using Tavily API.

    Returns ToolResult with data = {
        "results": [
            {"title": str, "url": str, "content": str, "score": float},
            ...
        ],
        "query": str,
        "result_count": int
    }
    """
    started = perf_counter()
    normalized_query = " ".join((query or "").strip().split())

    if not normalized_query:
        return ToolResult(
            success=True,
            data={"results": [], "query": "", "result_count": 0},
            error_message="",
            latency_ms=_latency_ms(started),
            tool_name="web_search",
        )

    tavily_api_key = _get_tavily_api_key()
    if not tavily_api_key:
        return ToolResult(
            success=False,
            data={"results": [], "query": normalized_query, "result_count": 0},
            error_message="Tavily API key not configured",
            latency_ms=_latency_ms(started),
            tool_name="web_search",
        )

    payload = {
        "api_key": tavily_api_key,
        "query": normalized_query,
        "max_results": max(1, int(max_results)),
        "search_depth": "advanced" if str(search_depth).strip().lower() == "advanced" else "basic",
        "include_domains": list(include_domains or []),
        "exclude_domains": list(exclude_domains or []),
    }

    try:
        timeout = httpx.Timeout(DEFAULT_SEARCH_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(TAVILY_SEARCH_URL, json=payload)
            response.raise_for_status()
            raw = response.json()
    except httpx.TimeoutException:
        return ToolResult(
            success=False,
            data={"results": [], "query": normalized_query, "result_count": 0},
            error_message="Search timed out",
            latency_ms=_latency_ms(started),
            tool_name="web_search",
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            data={"results": [], "query": normalized_query, "result_count": 0},
            error_message=str(exc),
            latency_ms=_latency_ms(started),
            tool_name="web_search",
        )

    results = _normalize_tavily_results(raw, max_results=max_results)
    return ToolResult(
        success=True,
        data={
            "results": results,
            "query": normalized_query,
            "result_count": len(results),
        },
        error_message="",
        latency_ms=_latency_ms(started),
        tool_name="web_search",
    )


def extract_search_query(step_description: str, context: str = "") -> str:
    """
    Extract a concise search query from a step description.
    - Remove filler words
    - Keep key terms
    - Limit to 10 words
    - If step description is too vague, use context to build query
    """
    description = " ".join((step_description or "").strip().split())
    cleaned_description = description
    for pattern in FILLER_PATTERNS:
        cleaned_description = re.sub(pattern, " ", cleaned_description, flags=re.IGNORECASE)

    description_terms = _extract_terms(cleaned_description)
    if _is_vague(description_terms) and context:
        context_terms = _extract_terms(context)
        description_terms.extend(term for term in context_terms if term not in description_terms)

    if not description_terms:
        fallback = _extract_terms(context or step_description)
        description_terms = fallback if fallback else ["latest", "relevant", "information"]

    return " ".join(description_terms[:10])


def format_search_results(tool_result: ToolResult) -> str:
    """
    Format search results as a string to inject into LLM prompts.

    Format:
    --- Web Search Results for "{query}" ---
    1. {title}
       URL: {url}
       {content[:300]}
    2. ...
    --- End of Search Results ---
    """
    query = ""
    results: list[dict[str, Any]] = []

    if isinstance(tool_result.data, dict):
        query = str(tool_result.data.get("query") or "")
        raw_results = tool_result.data.get("results")
        if isinstance(raw_results, list):
            results = [entry for entry in raw_results if isinstance(entry, dict)]

    lines = [f'--- Web Search Results for "{query}" ---']

    if not tool_result.success:
        lines.append(f"Error: {tool_result.error_message}")
        lines.append("--- End of Search Results ---")
        return "\n".join(lines)

    if not results:
        lines.append("No results found.")
        lines.append("--- End of Search Results ---")
        return "\n".join(lines)

    for index, result in enumerate(results, start=1):
        title = str(result.get("title") or "Untitled")
        url = str(result.get("url") or "")
        content = " ".join(str(result.get("content") or "").split())[:300]
        lines.append(f"{index}. {title}")
        lines.append(f"   URL: {url}")
        lines.append(f"   {content}")

    lines.append("--- End of Search Results ---")
    return "\n".join(lines)


def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    """Backward-compatible sync wrapper used by legacy executor call-sites."""
    started = perf_counter()
    normalized_query = " ".join((query or "").strip().split())

    tavily_api_key = _get_tavily_api_key()
    if not tavily_api_key:
        return {
            "success": False,
            "data": {"results": [], "query": normalized_query, "result_count": 0},
            "error_message": "Tavily API key not configured",
            "latency_ms": _latency_ms(started),
            "tool_name": "web_search",
        }

    payload = {
        "api_key": tavily_api_key,
        "query": normalized_query,
        "max_results": max(1, int(max_results)),
        "search_depth": "basic",
        "include_domains": [],
        "exclude_domains": [],
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(DEFAULT_SEARCH_TIMEOUT_SECONDS)) as client:
            response = client.post(TAVILY_SEARCH_URL, json=payload)
            response.raise_for_status()
            raw = response.json()
    except httpx.TimeoutException:
        return {
            "success": False,
            "data": {"results": [], "query": normalized_query, "result_count": 0},
            "error_message": "Search timed out",
            "latency_ms": _latency_ms(started),
            "tool_name": "web_search",
        }
    except Exception as exc:
        return {
            "success": False,
            "data": {"results": [], "query": normalized_query, "result_count": 0},
            "error_message": str(exc),
            "latency_ms": _latency_ms(started),
            "tool_name": "web_search",
        }

    results = _normalize_tavily_results(raw, max_results=max_results)
    return {
        "success": True,
        "data": {
            "results": results,
            "query": normalized_query,
            "result_count": len(results),
        },
        "error_message": "",
        "latency_ms": _latency_ms(started),
        "tool_name": "web_search",
    }


def _get_tavily_api_key() -> str:
    """Best-effort retrieval of Tavily API key from settings."""
    try:
        settings = get_settings()
    except Exception as exc:
        logger.warning("web_search_settings_unavailable error=%s", exc)
        return ""

    key = getattr(settings, "tavily_api_key", None)
    if key is None:
        key = getattr(settings, "TAVILY_API_KEY", None)
    return str(key or "").strip()


def _normalize_tavily_results(payload: Any, max_results: int) -> list[dict[str, Any]]:
    """Normalize Tavily API response payload to a stable result schema."""
    if not isinstance(payload, dict):
        return []

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_results[: max(1, int(max_results))]:
        if not isinstance(item, dict):
            continue

        score_value = item.get("score", 0.0)
        try:
            score = float(score_value)
        except (TypeError, ValueError):
            score = 0.0

        normalized.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or ""),
                "score": score,
            }
        )

    return normalized


def _extract_terms(text: str) -> list[str]:
    """Extract deduplicated key terms from text for query construction."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_/]*", (text or "").lower())
    terms: list[str] = []
    for token in tokens:
        if token in STOP_WORDS:
            continue
        if len(token) <= 1:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _is_vague(terms: list[str]) -> bool:
    """Decide whether extracted terms are too vague to form a useful query."""
    if len(terms) >= 2:
        return False
    return True


def _latency_ms(started: float) -> int:
    """Compute elapsed milliseconds from perf_counter start value."""
    return int((perf_counter() - started) * 1000)


__all__ = [
    "ToolResult",
    "extract_search_query",
    "format_search_results",
    "search_web",
    "web_search",
]
