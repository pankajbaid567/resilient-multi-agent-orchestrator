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
