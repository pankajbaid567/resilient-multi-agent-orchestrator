"""Per-call LLM usage records, collected without threading state through every function.

A node opens ``usage_scope(...)``; every ``call_llm`` made inside it (including from tasks
spawned within the scope) appends one record. The node returns the records as a state
delta, so token counts cover every call (planner, supervisor, workers, judge, reflector,
finalizer) instead of only the ones someone remembered to add up.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterator


@dataclass(slots=True)
class LLMCallRecord:
    """One model call as reported by the provider."""

    node: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    ok: bool
    step_id: str | None = None
    agent: str | None = None
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class _Scope:
    node: str
    step_id: str | None
    agent: str | None
    records: list[dict]


_current: ContextVar[_Scope | None] = ContextVar("llm_usage_scope", default=None)


@contextmanager
def usage_scope(node: str, step_id: str | None = None, agent: str | None = None) -> Iterator[list[dict]]:
    """Collect LLM call records made inside this block; yields the (live) record list.

    Nested scopes record into the innermost scope's list and also bubble up to the outer
    one, so a node-level scope sees calls made inside an agent-level scope.
    """
    parent = _current.get()
    records: list[dict] = []
    scope = _Scope(node=node, step_id=step_id, agent=agent, records=records)
    token = _current.set(scope)
    try:
        yield records
    finally:
        _current.reset(token)
        if parent is not None:
            parent.records.extend(records)


def record_llm_call(
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    ok: bool,
    error: str | None = None,
) -> None:
    """Append a record to the active scope; a no-op when no scope is open."""
    scope = _current.get()
    if scope is None:
        return
    scope.records.append(
        LLMCallRecord(
            node=scope.node,
            model=model,
            provider=provider,
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            latency_ms=max(0, int(latency_ms)),
            ok=ok,
            step_id=scope.step_id,
            agent=scope.agent,
            error=error,
        ).to_dict()
    )


def total_tokens(records: list[dict]) -> int:
    return sum(int(r.get("input_tokens", 0)) + int(r.get("output_tokens", 0)) for r in records)
