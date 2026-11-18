"""LangGraph checkpointer selection.

CHECKPOINT_URL picks where run state is saved after every super-step:
  memory              in-process (default; lost on restart, fine for tests and demos)
  sqlite:///path.db   a local file (survives restarts on a machine with a persistent disk)
  postgresql://...    Postgres, for example a free Neon database (survives redeploys)
"""

from __future__ import annotations

from contextlib import AsyncExitStack
import logging
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from config import get_settings

logger = logging.getLogger(__name__)


async def open_checkpointer(stack: AsyncExitStack) -> Any:
    """Open the configured checkpointer inside ``stack`` (closed when the stack closes)."""
    url = (get_settings().CHECKPOINT_URL or "memory").strip()
    if url.startswith("sqlite"):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = url.split("sqlite:///", 1)[-1] or "checkpoints.db"
        saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
        await saver.setup()
        logger.info("checkpointer=sqlite path=%s", path)
        return saver
    if url.startswith(("postgres://", "postgresql://")):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
        await saver.setup()
        logger.info("checkpointer=postgres")
        return saver
    logger.info("checkpointer=memory")
    return InMemorySaver()
