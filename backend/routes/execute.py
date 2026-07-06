"""Execution API routes for background execution and live event streaming."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.graph import resume_agent
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])

_running_tasks: dict[str, asyncio.Task[None]] = {}


class ExecuteData(BaseModel):
    """Data payload returned after scheduling task execution."""

    status: str


class ExecuteResponse(BaseModel):
    """Envelope for execution start endpoint responses."""

    success: bool
    data: ExecuteData | None = None
    error: str | None = None


@router.post("/tasks/{task_id}/execute", response_model=ExecuteResponse)
async def execute_task(task_id: str):
    """Load checkpoint and start full graph execution in background."""
    try:
        redis = get_redis_service()
        state = await redis.load_checkpoint(task_id)
        if state is None:
            return _error_response(status_code=404, message=f"Task not found: {task_id}")

        _start_background_execution(task_id)
        return ExecuteResponse(success=True, data=ExecuteData(status="started"), error=None)
    except Exception as exc:
        logger.exception("execute_task_failed task_id=%s error=%s", task_id, exc)
        return _error_response(status_code=500, message=str(exc))


@router.websocket("/ws/{task_id}")
async def task_events(ws: WebSocket, task_id: str) -> None:
    """Forward Redis pub/sub task events to WebSocket clients with heartbeat pings."""
    await ws.accept()
    redis = get_redis_service()
    stop_event = asyncio.Event()

    async def forward_pubsub_events() -> None:
        """Forward every Redis event to the connected websocket client."""
        try:
            async for event in redis.subscribe_events(task_id):
                if stop_event.is_set():
                    break

                await ws.send_json(event)

                event_type = str(event.get("event_type") or "")
                if event_type in {"task_completed", "task_failed"}:
                    stop_event.set()
                    break
        except WebSocketDisconnect:
            stop_event.set()
        except Exception as exc:  # pragma: no cover - websocket bridge runtime guard
            logger.warning("ws_forward_failed task_id=%s error=%s", task_id, exc)
            if not stop_event.is_set():
                try:
                    await ws.send_json(
                        {
                            "event_type": "ws_error",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "task_id": task_id,
                            "data": {"error": str(exc)},
                        }
                    )
                except Exception:
                    pass
            stop_event.set()

    async def heartbeat() -> None:
        """Send websocket heartbeat ping every 30 seconds until stop signal."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await ws.send_json(
                        {
                            "event_type": "ping",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "task_id": task_id,
                            "data": {},
                        }
                    )
                except WebSocketDisconnect:
                    stop_event.set()
                    break
                except Exception:
                    stop_event.set()
                    break

    async def receive_loop() -> None:
        """Read inbound websocket frames to detect disconnects promptly."""
        while not stop_event.is_set():
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                stop_event.set()
                break
            except Exception:
                stop_event.set()
                break

    tasks = [
        asyncio.create_task(forward_pubsub_events()),
        asyncio.create_task(heartbeat()),
        asyncio.create_task(receive_loop()),
    ]

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close(code=1000)
        except Exception:
            pass


def _start_background_execution(task_id: str) -> None:
    """Start a single background execution task per task_id."""
    existing = _running_tasks.get(task_id)
    if existing is not None and not existing.done():
        return

    task = asyncio.create_task(_run_execution(task_id))
    _running_tasks[task_id] = task


async def _run_execution(task_id: str) -> None:
    """Run resume flow and publish terminal completion/failure event."""
    redis = get_redis_service()

    try:
        final_state = await resume_agent(task_id)
        await redis.save_checkpoint(task_id, final_state)

        final_status = str(final_state.get("status") or "failed").lower()
        event_type = "task_completed" if final_status == "completed" else "task_failed"
        await redis.publish_event(
            task_id,
            {
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "data": {
                    "status": final_state.get("status"),
                    "current_step_index": final_state.get("current_step_index"),
                },
            },
        )
    except Exception as exc:  # pragma: no cover - background execution safety
        logger.exception("background_execution_failed task_id=%s error=%s", task_id, exc)
        try:
            await redis.publish_event(
                task_id,
                {
                    "event_type": "task_failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": task_id,
                    "data": {"status": "failed", "error": str(exc)},
                },
            )
        except Exception:
            pass
    finally:
        running = _running_tasks.get(task_id)
        if running is not None and running.done():
            _running_tasks.pop(task_id, None)
        elif running is not None and running.cancelled():
            _running_tasks.pop(task_id, None)
        else:
            _running_tasks.pop(task_id, None)


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
