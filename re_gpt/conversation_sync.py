"""Transport-owned exact-graph revision watcher."""
from __future__ import annotations

import threading
from collections.abc import Callable

from .pg_hydration import hydrate_one


class ConversationRevisionWatcher:
    """Poll one exact graph and publish only revision advances to subscribers."""

    def __init__(self, *, interval: float = 15.0, hydrate: Callable = hydrate_one):
        self.interval = interval
        self.hydrate = hydrate
        self._callbacks: dict[str, Callable[[dict], None]] = {}
        self._revisions: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def watch(self, conversation_id: str, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._callbacks[conversation_id] = callback
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="re-gpt-conversation-revisions")
                self._thread.start()

    def unwatch(self, conversation_id: str) -> None:
        with self._lock:
            self._callbacks.pop(conversation_id, None)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                watched = list(self._callbacks.items())
            for conversation_id, callback in watched:
                try:
                    receipt = self.hydrate(conversation_id)
                    revision = str(receipt.get("revision") or "")
                    previous = self._revisions.get(conversation_id)
                    self._revisions[conversation_id] = revision
                    if previous is not None and revision != previous:
                        callback(receipt)
                except Exception:
                    continue
