"""Track fire-and-forget asyncio tasks so they aren't GC'd mid-flight.

Stdlib asyncio.create_task() only weakly references the returned task; without an
extra strong reference the task can be garbage-collected before completion. Code
here keeps a module-level set of strong refs and discards each one on completion,
logging any unhandled exception. Also used for long-running periodic tasks (e.g.
the session-TTL sweeper) that must outlive any single request.
"""

import asyncio
from typing import Coroutine, Optional, Set

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_tasks: Set[asyncio.Task] = set()


def track(coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
    """Schedule a coroutine and keep a strong reference until it completes."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %r raised: %s", task.get_name(), exc, exc_info=exc)


def pending_count() -> int:
    return len(_tasks)


async def cancel_all() -> None:
    """Cancel all tracked tasks; intended for FastAPI lifespan shutdown."""
    if not _tasks:
        return
    for t in list(_tasks):
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()
