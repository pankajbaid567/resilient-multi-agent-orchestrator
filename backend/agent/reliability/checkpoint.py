"""Checkpoint persistence helpers for saving and restoring agent state."""

from __future__ import annotations

import json
from collections.abc import Mapping

from services import get_redis_client


class CheckpointStore:
    """Redis-backed checkpoint store for agent task state."""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self.ttl_seconds = ttl_seconds

    async def save(self, task_id: str, state: Mapping[str, object]) -> None:
        """Persist serialized task state under a deterministic Redis key."""
        client = await get_redis_client()
        key = f"task:{task_id}:state"
        await client.set(key, json.dumps(state, default=str), ex=self.ttl_seconds)

    async def load(self, task_id: str) -> dict[str, object] | None:
        """Load previously persisted task state if available."""
        client = await get_redis_client()
        key = f"task:{task_id}:state"
        raw = await client.get(key)
        if not raw:
            return None
        return json.loads(raw)


# TODO: Add pub/sub state event publishing for real-time UI updates.
