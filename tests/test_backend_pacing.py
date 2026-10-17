from __future__ import annotations

import asyncio
import json

from re_gpt import backend_pacing
from re_gpt.stream_runtime import stream_runtime


class FakeLimiter:
    def __init__(self) -> None:
        self.acquired = []
        self.success = []
        self.backoffs = 0

    def acquire(self, *, kind="backend", min_interval=None):
        self.acquired.append(kind)
        return {"waited_seconds": 0.0}

    def report_success(self, *, status=200):
        self.success.append(status)
        return {}

    def report_429(self, *, retry_after=None):
        self.backoffs += 1
        return {}


class Response:
    status_code = 200
    headers = {}


class FakeAsyncSession:
    cookies = {}

    async def post(self, *args, **kwargs):
        callback = kwargs.get("content_callback")
        if callback:
            callback(("data: " + json.dumps({
                "message": {
                    "id": "a1",
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["hi"]},
                    "status": "in_progress",
                }
            }) + "\n\n").encode())
            callback(b"data: [DONE]\n\n")
        return Response()


def test_paced_async_session_emits_stream_progress(monkeypatch) -> None:
    limiter = FakeLimiter()
    monkeypatch.setattr(backend_pacing, "GlobalBackendRateLimiter", lambda: limiter)
    session = backend_pacing.PacedAsyncSession(FakeAsyncSession())
    progress = []

    async def run():
        with stream_runtime(progress.append) as state:
            await session.post(
                url="https://chatgpt.com/backend-api/conversation",
                content_callback=lambda chunk: None,
            )
            return state.summary()

    summary = asyncio.run(run())
    assert limiter.acquired == ["backend"]
    assert limiter.success == [200]
    assert [event["kind"] for event in progress] == ["token_delta", "terminal"]
    assert summary["stream_terminal_observed"] is True
    assert summary["assistant_stream_text"] == "hi"
