from __future__ import annotations

import asyncio

from re_gpt.async_bridge import run_async_sync


def test_run_async_sync_without_running_loop() -> None:
    async def work() -> str:
        await asyncio.sleep(0)
        return "ok"

    assert run_async_sync(work) == "ok"


def test_run_async_sync_from_running_loop_uses_worker_thread() -> None:
    async def work() -> str:
        await asyncio.sleep(0)
        return "ok"

    async def caller() -> str:
        return run_async_sync(work)

    assert asyncio.run(caller()) == "ok"
