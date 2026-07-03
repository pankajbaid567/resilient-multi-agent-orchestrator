"""Redis service layer with resilient in-memory fallback behavior."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from redis.asyncio import Redis  # type: ignore[reportMissingImports]
from redis.exceptions import ConnectionError as RedisConnectionError  # type: ignore[reportMissingImports]
from redis.exceptions import RedisError  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 86400
CIRCUIT_TTL_SECONDS = 300
ALLOWED_STEP_STATUSES = {
    "pending",
    "running",
    "success",
    "failed",
    "retrying",
    "reflecting",
    "skipped",
}
DEFAULT_CIRCUIT_STATE = {
    "state": "closed",
    "failure_count": 0,
    "success_count": 0,
    "last_failure_time": None,
    "opened_at": None,
}


class RedisService:
    """Manages Redis connections and state operations with in-memory fallback."""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._client: Redis | None = None
        self._connected = False
        self._connect_lock = asyncio.Lock()
        self._fallback_warning_logged = False

        # In-memory fallback stores.
        self._state_store: dict[str, str] = {}
        self._step_store: dict[str, str] = {}
        self._event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    async def connect(self, retries: int = 3, delay: float = 1.0) -> bool:
        """Establish Redis connection with retries. Returns True if connected, False if falling back to memory."""
        async with self._connect_lock:
            if self._client is None:
                self._client = Redis.from_url(self.redis_url, decode_responses=True)

            for attempt in range(1, retries + 1):
                try:
                    await self._client.ping()
                    self._connected = True
                    return True
                except (RedisConnectionError, RedisError) as exc:
                    if attempt < retries:
                        logger.warning("Redis connection failed (attempt %d/%d): %s. Retrying in %ss...", attempt, retries, exc, delay)
                        await asyncio.sleep(delay)
                    else:
                        self._mark_fallback(exc)
                        await self._safe_close_client()
                        return False
            return False

    async def disconnect(self):
        """Close Redis connection pool."""
        async with self._connect_lock:
            if self._client is None:
                self._connected = False
                return

            try:
                await self._client.aclose()
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)
            finally:
                self._client = None
                self._connected = False

    async def is_connected(self) -> bool:
        """Ping Redis. Returns False if unreachable."""
        client = await self._get_client()
        if client is None:
            return False

        try:
            await client.ping()
            self._connected = True
            return True
        except (RedisConnectionError, RedisError) as exc:
            self._mark_fallback(exc)
            await self._safe_close_client()
            return False

    # --- State Checkpointing ---

    async def save_checkpoint(self, task_id: str, state: dict) -> None:
        """Save full AgentState to Redis with 24h TTL.
        Key: task:{task_id}:state
        Falls back to in-memory dict if Redis unavailable."""
        key = self._state_key(task_id)
        payload = self._serialize(state)
        client = await self._get_client()

        if client is not None:
            try:
                await client.set(key, payload, ex=STATE_TTL_SECONDS)
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        self._state_store[key] = payload

    async def load_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Load AgentState from Redis.
        Returns None if task doesn't exist."""
        key = self._state_key(task_id)
        client = await self._get_client()

        if client is not None:
            try:
                payload = await client.get(key)
                if payload is None:
                    return None
                loaded = self._deserialize(payload)
                return loaded if isinstance(loaded, dict) else None
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        payload = self._state_store.get(key)
        if payload is None:
            return None

        loaded = self._deserialize(payload)
        return loaded if isinstance(loaded, dict) else None

    async def delete_checkpoint(self, task_id: str) -> None:
        """Delete a task's checkpoint."""
        key = self._state_key(task_id)
        client = await self._get_client()

        if client is not None:
            try:
                await client.delete(key)
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        self._state_store.pop(key, None)

    # --- Step Status ---

    async def update_step_status(self, task_id: str, step_index: int, status: str) -> None:
        """Update individual step status.
        Key: task:{task_id}:step:{step_index}:status
        Status values: pending, running, success, failed, retrying, reflecting, skipped"""
        if status not in ALLOWED_STEP_STATUSES:
            raise ValueError(f"Unsupported step status: {status}")

        key = self._step_status_key(task_id, step_index)
        client = await self._get_client()

        if client is not None:
            try:
                await client.set(key, status, ex=STATE_TTL_SECONDS)
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        self._step_store[key] = status

    async def get_step_status(self, task_id: str, step_index: int) -> str | None:
        """Get single step's status without loading full state."""
        key = self._step_status_key(task_id, step_index)
        client = await self._get_client()

        if client is not None:
            try:
                status = await client.get(key)
                if status is None:
                    return None
                return str(status)
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        return self._step_store.get(key)

    # --- Pub/Sub ---

    async def publish_event(self, task_id: str, event: dict) -> None:
        """Publish event to channel task:{task_id}:events.
        Event must include: event_type, timestamp, and event-specific data.
        Falls back to asyncio.Queue in memory mode."""
        if "event_type" not in event or "timestamp" not in event:
            raise ValueError("event must include 'event_type' and 'timestamp'")

        channel = self._events_channel(task_id)
        payload = self._serialize(event)
        client = await self._get_client()

        if client is not None:
            try:
                await client.publish(channel, payload)
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        queue = self._event_queues.setdefault(task_id, asyncio.Queue())
        normalized = self._deserialize(payload)
        if isinstance(normalized, dict):
            await queue.put(normalized)

    async def subscribe_events(self, task_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to task events. Yields parsed JSON events.
        In memory mode, reads from asyncio.Queue."""
        channel = self._events_channel(task_id)
        client = await self._get_client()

        if client is not None:
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue

                    raw_payload = message.get("data")
                    if raw_payload is None:
                        continue

                    event = self._deserialize(raw_payload)
                    if isinstance(event, dict):
                        yield event
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                except (RedisConnectionError, RedisError):
                    pass
                try:
                    await pubsub.aclose()
                except (RedisConnectionError, RedisError):
                    pass

        queue = self._event_queues.setdefault(task_id, asyncio.Queue())
        while True:
            event = await queue.get()
            yield event

    # --- Circuit Breaker State ---

    async def get_circuit_state(self, provider: str) -> dict[str, Any]:
        """Get circuit breaker state for an LLM provider.
        Key: circuit:{provider}
        Returns: {state, failure_count, success_count, last_failure_time, opened_at}"""
        key = self._circuit_key(provider)
        client = await self._get_client()

        if client is not None:
            try:
                payload = await client.get(key)
                if payload is None:
                    return dict(DEFAULT_CIRCUIT_STATE)

                loaded = self._deserialize(payload)
                if isinstance(loaded, dict):
                    return self._normalize_circuit_state(loaded)
                return dict(DEFAULT_CIRCUIT_STATE)
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        payload = self._state_store.get(key)
        if payload is None:
            return dict(DEFAULT_CIRCUIT_STATE)

        loaded = self._deserialize(payload)
        if isinstance(loaded, dict):
            return self._normalize_circuit_state(loaded)
        return dict(DEFAULT_CIRCUIT_STATE)

    async def set_circuit_state(self, provider: str, state: dict) -> None:
        """Update circuit breaker state. TTL: 300 seconds."""
        key = self._circuit_key(provider)
        normalized = self._normalize_circuit_state(state)
        payload = self._serialize(normalized)
        client = await self._get_client()

        if client is not None:
            try:
                await client.set(key, payload, ex=CIRCUIT_TTL_SECONDS)
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        self._state_store[key] = payload

    async def _get_client(self) -> Redis | None:
        """Get a ready Redis client, creating/connecting it when needed."""
        if self._client is None:
            connected = await self.connect()
            return self._client if connected else None

        return self._client

    async def _safe_close_client(self) -> None:
        """Best-effort close of the Redis client after connectivity errors."""
        if self._client is None:
            return

        try:
            await self._client.aclose()
        except (RedisConnectionError, RedisError):
            pass
        finally:
            self._client = None
            self._connected = False

    def _mark_fallback(self, error: Exception) -> None:
        """Mark service as disconnected and emit one fallback warning."""
        self._connected = False
        if not self._fallback_warning_logged:
            logger.warning("Redis unavailable; switching to in-memory fallback. reason=%s", error)
            self._fallback_warning_logged = True

    @staticmethod
    def _json_default(value: Any) -> Any:
        """JSON serializer with datetime support for state and event payloads."""
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "model_dump"):
            return value.model_dump()  # pydantic models
        return str(value)

    def _serialize(self, payload: Any) -> str:
        """Serialize Python payloads into JSON strings."""
        return json.dumps(payload, default=self._json_default)

    @staticmethod
    def _deserialize(raw_payload: str | bytes) -> Any:
        """Parse Redis strings/bytes into Python payloads."""
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")
        return json.loads(raw_payload)

    @staticmethod
    def _normalize_circuit_state(state: dict[str, Any]) -> dict[str, Any]:
        """Merge provided circuit fields with a stable default schema."""
        normalized = dict(DEFAULT_CIRCUIT_STATE)
        for key in DEFAULT_CIRCUIT_STATE:
            if key in state and state[key] is not None:
                normalized[key] = state[key]
        return normalized

    @staticmethod
    def _state_key(task_id: str) -> str:
        return f"task:{task_id}:state"

    @staticmethod
    def _step_status_key(task_id: str, step_index: int) -> str:
        return f"task:{task_id}:step:{step_index}:status"

    @staticmethod
    def _events_channel(task_id: str) -> str:
        return f"task:{task_id}:events"

    @staticmethod
    def _circuit_key(provider: str) -> str:
        return f"circuit:{provider}"


_redis_service: RedisService | None = None


def get_redis_service() -> RedisService:
    """Return a singleton RedisService instance for the process."""
    global _redis_service
    if _redis_service is None:
        redis_url = "redis://localhost:6379"
        try:
            from config import get_settings

            redis_url = get_settings().redis_url
        except Exception as exc:  # pragma: no cover - defensive bootstrap fallback
            logger.warning("Using default Redis URL due to settings load failure: %s", exc)
        _redis_service = RedisService(redis_url=redis_url)
    return _redis_service


async def get_redis_client() -> Redis:
    """Backward-compatible helper returning the underlying connected Redis client."""
    service = get_redis_service()
    client = await service._get_client()
    if client is None:
        raise RuntimeError("Redis is unavailable; running in in-memory fallback mode")
    return client


async def close_redis_client() -> None:
    """Backward-compatible helper to close the shared RedisService client."""
    await get_redis_service().disconnect()
