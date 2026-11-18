"""Redis service layer with resilient in-memory fallback behavior."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from redis.asyncio import Redis  # type: ignore[reportMissingImports]
from redis.exceptions import ConnectionError as RedisConnectionError  # type: ignore[reportMissingImports]
from redis.exceptions import RedisError  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 86400
CIRCUIT_TTL_SECONDS = 300
RECONNECT_INTERVAL_SECONDS = 30.0
MAX_STREAM_EVENTS = 1_000
MAX_TASK_LOGS = 200
STREAM_BLOCK_MS = 15_000
TERMINAL_EVENTS = {"task_completed", "task_failed"}
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
        # After a failed connect, skip reconnect attempts until this monotonic time, so a
        # missing Redis costs one fast check per interval instead of seconds per operation.
        self._next_connect_attempt = 0.0

        # In-memory fallback stores.
        self._state_store: dict[str, str] = {}
        self._step_store: dict[str, str] = {}
        self._event_logs: dict[str, list[dict[str, Any]]] = {}
        self._event_conditions: dict[str, asyncio.Condition] = {}

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

    # --- Live events (Redis Streams) ---
    #
    # Events go to a per-task stream (XADD, capped length, 24 h TTL). A subscriber reads
    # from the beginning of the stream, so a client that connects late, or reconnects,
    # replays everything it missed; plain pub/sub dropped those events. Without Redis, a
    # bounded in-memory log per task gives the same replay semantics within one process.

    async def publish_event(self, task_id: str, event: dict) -> None:
        """Append an event (must include event_type and timestamp) to the task's stream."""
        if "event_type" not in event or "timestamp" not in event:
            raise ValueError("event must include 'event_type' and 'timestamp'")

        payload = self._serialize(event)
        client = await self._get_client()
        if client is not None:
            try:
                key = self._events_stream(task_id)
                await client.xadd(key, {"data": payload}, maxlen=MAX_STREAM_EVENTS, approximate=True)
                await client.expire(key, STATE_TTL_SECONDS)
                return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        normalized = self._deserialize(payload)
        if not isinstance(normalized, dict):
            return
        log = self._event_logs.setdefault(task_id, [])
        log.append(normalized)
        if len(log) > MAX_STREAM_EVENTS:
            del log[: len(log) - MAX_STREAM_EVENTS]
        while len(self._event_logs) > MAX_TASK_LOGS:
            self._event_logs.pop(next(iter(self._event_logs)))
        condition = self._event_conditions.setdefault(task_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    async def subscribe_events(self, task_id: str, from_start: bool = True) -> AsyncGenerator[dict[str, Any], None]:
        """Yield the task's events (replaying earlier ones), ending after a terminal event."""
        client = await self._get_client()
        if client is not None:
            key = self._events_stream(task_id)
            last_id = "0-0" if from_start else "$"
            try:
                while True:
                    response = await client.xread({key: last_id}, block=STREAM_BLOCK_MS, count=100)
                    for _stream, entries in response or []:
                        for entry_id, fields in entries:
                            last_id = entry_id
                            event = self._deserialize(fields.get("data", "{}"))
                            if isinstance(event, dict):
                                yield event
                                if str(event.get("event_type") or "") in TERMINAL_EVENTS:
                                    return
            except (RedisConnectionError, RedisError) as exc:
                self._mark_fallback(exc)

        index = 0 if from_start else len(self._event_logs.get(task_id, []))
        condition = self._event_conditions.setdefault(task_id, asyncio.Condition())
        while True:
            log = self._event_logs.get(task_id, [])
            while index < len(log):
                event = log[index]
                index += 1
                yield event
                if str(event.get("event_type") or "") in TERMINAL_EVENTS:
                    return
            async with condition:
                await condition.wait()

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
        if self._client is not None:
            return self._client

        now = time.monotonic()
        if now < self._next_connect_attempt:
            return None

        connected = await self.connect(retries=1, delay=0.0)
        if not connected:
            self._next_connect_attempt = now + RECONNECT_INTERVAL_SECONDS
            return None
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
    def _step_status_key(task_id: str, step_index: int) -> str:
        return f"task:{task_id}:step:{step_index}:status"

    @staticmethod
    def _events_stream(task_id: str) -> str:
        return f"task:{task_id}:stream"

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
