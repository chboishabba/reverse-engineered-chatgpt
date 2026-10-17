"""Operation-local stream progress context shared with HTTP transport wrappers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from .stream_follow import ProgressCallback, SSEProgressDecoder


@dataclass
class StreamRuntimeState:
    decoder: SSEProgressDecoder = field(default_factory=SSEProgressDecoder)
    progress_callback: ProgressCallback | None = None
    follow_requested: bool = False

    def feed(self, chunk: bytes | str) -> None:
        events = self.decoder.feed(chunk)
        if self.progress_callback is None:
            return
        for event in events:
            self.progress_callback(dict(event))

    def summary(self) -> dict[str, Any]:
        return self.decoder.summary()


_CURRENT_STREAM_STATE: ContextVar[StreamRuntimeState | None] = ContextVar(
    "re_gpt_stream_runtime_state",
    default=None,
)


def current_stream_state() -> StreamRuntimeState | None:
    return _CURRENT_STREAM_STATE.get()


@contextmanager
def stream_runtime(
    progress_callback: ProgressCallback | None = None,
    *,
    follow_requested: bool = False,
) -> Iterator[StreamRuntimeState]:
    state = StreamRuntimeState(
        progress_callback=progress_callback,
        follow_requested=bool(follow_requested),
    )
    token = _CURRENT_STREAM_STATE.set(state)
    try:
        yield state
    finally:
        _CURRENT_STREAM_STATE.reset(token)
