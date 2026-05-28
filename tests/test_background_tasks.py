"""Unit tests for app.services.background_tasks (R-TEST-7).

Covers the strong-reference tracking, the _on_done error-logging branch, and
cancel_all (the lifespan-shutdown path) — previously uncovered.
"""

import asyncio

import pytest

from app.services import background_tasks


@pytest.fixture(autouse=True)
def _clean_task_set():
    # background_tasks keeps a module-level set; isolate tests from each other.
    background_tasks._tasks.clear()
    yield
    background_tasks._tasks.clear()


async def test_track_runs_and_discards_on_completion():
    ran = asyncio.Event()

    async def work():
        ran.set()
        return "done"

    task = background_tasks.track(work(), name="unit-work")
    assert task in background_tasks._tasks  # strong ref held while running
    await task
    await asyncio.sleep(0)  # let the done-callback fire
    assert ran.is_set()
    assert task not in background_tasks._tasks  # discarded after completion


async def test_on_done_handles_unhandled_exception(monkeypatch):
    # Capture that _on_done logs the failure (caplog is unavailable under -p no:logging,
    # so assert via a logger spy instead).
    logged = []
    monkeypatch.setattr(background_tasks.logger, "error",
                        lambda *a, **k: logged.append((a, k)))

    async def boom():
        raise ValueError("kaboom")

    task = background_tasks.track(boom(), name="exploding")
    with pytest.raises(ValueError):
        await task
    await asyncio.sleep(0)  # let the done-callback fire
    assert task not in background_tasks._tasks   # discarded even on failure
    assert logged, "_on_done should log the unhandled exception"


async def test_cancel_all_cancels_running_tasks():
    started = asyncio.Event()

    async def long_runner():
        started.set()
        await asyncio.sleep(3600)  # would hang forever without cancellation

    task = background_tasks.track(long_runner(), name="long")
    await started.wait()
    assert background_tasks.pending_count() == 1

    await background_tasks.cancel_all()
    assert background_tasks.pending_count() == 0
    assert task.cancelled()


async def test_cancel_all_with_no_tasks_is_safe():
    assert background_tasks.pending_count() == 0
    await background_tasks.cancel_all()  # must not raise
    assert background_tasks.pending_count() == 0
