"""Run async callables safely from synchronous library and MCP entrypoints."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


def run_async_sync(factory: Callable[[], Awaitable[T]]) -> T:
    """Run one coroutine factory from sync code, including inside an active loop.

    When the caller already owns an event loop, execute the coroutine in a
    dedicated worker thread so the synchronous API never nests ``asyncio.run``
    on the active loop thread. Context variables are copied into that worker so
    stream-progress callbacks remain operation-local under concurrent MCP calls.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="re-gpt-async") as pool:
        return pool.submit(lambda: context.run(lambda: asyncio.run(factory()))).result()
