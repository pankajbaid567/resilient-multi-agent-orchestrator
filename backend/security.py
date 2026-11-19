"""Access control for endpoints that change process-wide runtime settings."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from config import get_settings


async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Allow the request only when ADMIN_API_KEY is configured and the header matches it.

    Runtime settings (chaos mode, retries, provider chain) are shared by every task in the
    process, so changing them is an admin action. With no ADMIN_API_KEY set, every change is
    refused: a public deployment is read-only by default.
    """
    expected = (get_settings().ADMIN_API_KEY or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runtime configuration changes are disabled on this deployment.",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key.strip(), expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key.")


# ---- Abuse protection for the public demo ------------------------------------------------
# Every task costs real model tokens, so a public deployment limits how many tasks one client
# can start and how many the whole service starts per day. In-memory, per process.

import time as _time
from collections import deque as _deque

from fastapi import Request

_per_ip: dict[str, _deque] = {}
_daily: dict[str, int] = {}


def _client_ip(request: Request) -> str:
    if get_settings().TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_task_creation(request: Request) -> None:
    """Refuse a new task when this client or the whole service is over its limit."""
    settings = get_settings()
    now = _time.time()
    per_hour = int(settings.TASKS_PER_IP_PER_HOUR)
    if per_hour > 0:
        window = _per_ip.setdefault(_client_ip(request), _deque())
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= per_hour:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"Limit of {per_hour} tasks per hour reached. Try again later.")
    daily = int(settings.DAILY_TASK_LIMIT)
    day = _time.strftime("%Y-%m-%d", _time.gmtime(now))
    if daily > 0 and _daily.get(day, 0) >= daily:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="This demo's daily task limit is reached. Try again tomorrow.")
    if per_hour > 0:
        _per_ip[_client_ip(request)].append(now)
    if daily > 0:
        for old_day in [d for d in _daily if d != day]:
            _daily.pop(old_day)
        _daily[day] = _daily.get(day, 0) + 1
    if len(_per_ip) > 10_000:
        _per_ip.clear()
