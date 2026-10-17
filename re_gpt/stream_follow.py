"""Incremental ChatGPT SSE progress decoding for stream-first follow."""

from __future__ import annotations

from collections import Counter
import json
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[dict[str, Any]], None]


class SSEProgressDecoder:
    """Decode arbitrarily chunked SSE into compact real-time progress events."""

    def __init__(self) -> None:
        self._buffer = ""
        self._last_text: dict[str, str] = {}
        self._counts: Counter[str] = Counter()
        self.conversation_id: str | None = None
        self.user_message_id: str | None = None
        self.assistant_message_id: str | None = None
        self.assistant_text = ""
        self.terminal_observed = False
        self.terminal_reason: str | None = None

    @property
    def event_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def _emit(self, event: dict[str, Any], output: list[dict[str, Any]]) -> None:
        kind = str(event.get("kind") or "event")
        self._counts[kind] += 1
        output.append(event)

    def _terminal(self, reason: str, output: list[dict[str, Any]]) -> None:
        if self.terminal_observed:
            return
        self.terminal_observed = True
        self.terminal_reason = reason
        self._emit({"kind": "terminal", "reason": reason}, output)

    def _handle_json(self, event: Mapping[str, Any], output: list[dict[str, Any]]) -> None:
        if event.get("conversation_id"):
            self.conversation_id = str(event["conversation_id"])

        top_status = str(event.get("status") or "").strip().lower()
        if top_status == "finished_successfully":
            self._terminal("finished_successfully", output)

        message = event.get("message")
        if not isinstance(message, Mapping):
            return

        message_id = str(message.get("id") or "").strip() or None
        author = message.get("author")
        role = str(author.get("role") or "").strip() if isinstance(author, Mapping) else ""
        if role == "user" and message_id:
            self.user_message_id = message_id
        elif role == "assistant" and message_id:
            self.assistant_message_id = message_id

        content = message.get("content")
        content = content if isinstance(content, Mapping) else {}
        content_type = str(content.get("content_type") or "").strip().lower()
        channel = str(message.get("channel") or "").strip().lower()
        recipient = str(message.get("recipient") or "").strip()
        status = str(message.get("status") or "").strip().lower()

        if channel == "analysis" or content_type in {"thoughts", "reasoning", "reasoning_recap"}:
            self._emit({
                "kind": "thinking",
                "message_id": message_id,
                "content_type": content_type or None,
            }, output)

        if (
            "tool" in content_type
            or (recipient and recipient not in {"all", "assistant", "user"})
            or content_type in {"computer_initialize_state", "computer_output"}
        ):
            self._emit({
                "kind": "tool_invocation",
                "message_id": message_id,
                "recipient": recipient or None,
                "content_type": content_type or None,
            }, output)

        parts = content.get("parts")
        text = ""
        if isinstance(parts, list):
            text = "".join(part for part in parts if isinstance(part, str))
        if role == "assistant" and message_id and text:
            prior = self._last_text.get(message_id, "")
            if text.startswith(prior):
                delta = text[len(prior):]
            else:
                delta = text
            self._last_text[message_id] = text
            self.assistant_text = text
            if delta:
                self._emit({
                    "kind": "token_delta",
                    "message_id": message_id,
                    "delta": delta,
                    "channel": channel or None,
                }, output)

        if status == "finished_successfully" and bool(message.get("end_turn")):
            self._terminal("finished_successfully", output)

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        self._buffer += text.replace("\r\n", "\n")
        output: list[dict[str, Any]] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body:
                continue
            if body == "[DONE]":
                self._terminal("done", output)
                continue
            try:
                decoded = json.loads(body)
            except Exception:
                continue
            if isinstance(decoded, Mapping):
                self._handle_json(decoded, output)
        return output

    def summary(self) -> dict[str, Any]:
        return {
            "stream_terminal_observed": self.terminal_observed,
            "stream_terminal_reason": self.terminal_reason,
            "stream_event_counts": self.event_counts,
            "conversation_id": self.conversation_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "assistant_stream_text": self.assistant_text or None,
        }
