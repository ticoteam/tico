# pyright: strict
"""Needs you's own lane: the threads that build a person's Needs you list for its routes (I39, AC-3).

A list is all Python work over a read snapshot. Built on the loop's shared pool, fifty readers at once ran fifty builds
at once, each giving the interpreter lock up at every SQLite statement and waiting to get it back: on a 1,500-task
company the route served 45 lists a second with 50 readers against 160 with one. Builds take turns here instead, as
many at once as there are lane threads (`LANES`), and a request waiting its turn holds none of the shared pool's
threads, so the routes beside it keep theirs. Each build still opens its own read snapshot and builds its own reader's
list: nothing is shared between readers.

The app's lifespan makes the pool (`start`) and shuts it down on the way out (`stop`), like sign-in's own pool; outside
a lifespan (a test client that does not run it) a build goes to the loop's default pool."""

import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")

LANES = 2  # two at once served the most lists a second: one and four served fewer (bench/needs-you-load.py)


def start(app: Any) -> ThreadPoolExecutor:
    pool = ThreadPoolExecutor(max_workers=LANES, thread_name_prefix="tico-needs-you")
    app.state.needs_lane = pool
    return pool


def stop(app: Any, pool: ThreadPoolExecutor) -> None:
    # A test can run the same app's lifespan inside another: clear the slot only while it is still this run's.
    if getattr(app.state, "needs_lane", None) is pool:
        app.state.needs_lane = None
    pool.shutdown(wait=False, cancel_futures=True)


async def run(app: Any, build: Callable[..., T], *args: Any) -> T:
    """`build(*args)` on the lane, with the request's context (its identity, timing and audit marks)."""
    pool = getattr(app.state, "needs_lane", None)
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(pool, lambda: context.run(build, *args))
