"""Incremental ChatGPT SSE progress decoding for stream-first follow."""

from __future__ import annotations

from collections import Counter
import codecs
import json
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[dict[str, Any]], None]


class SSEProgressDecoder:
    """Decode arbitrarily chunked SSE into compact real-time progress events."""

    def __init__(self) -> None:
        self._buffer = ""
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")("replace")
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
        conv_id = event.get("conversation_id")
        v = event.get("v")
        if not conv_id and isinstance(v, Mapping):
            conv_id = v.get("conversation_id")
        if conv_id:
            self.conversation_id = str(conv_id)

        msg_type = str(event.get("type") or "").strip().lower()
        if msg_type in {"message_stream_complete", "conversation_completed"}:
            self._terminal(msg_type, output)

        top_status = str(event.get("status") or "").strip().lower()
        if top_status == "finished_successfully":
            self._terminal("finished_successfully", output)

        patches: list[Mapping[str, Any]] = []
        if isinstance(v, list):
            patches.extend(item for item in v if isinstance(item, Mapping))
        elif "p" in event:
            patches.append(event)

        for patch in patches:
            p = str(patch.get("p") or "")
            op = str(patch.get("o") or "")
            pval = patch.get("v")
            if p == "/message/status" and str(pval).lower() == "finished_successfully":
                self._terminal("finished_successfully", output)
            elif p == "/message/end_turn" and bool(pval):
                self._terminal("finished_successfully", output)
            elif p.startswith("/message/content/parts/") or p == "/message/content/parts":
                delta_str = str(pval) if pval is not None else ""
                if op == "append" and delta_str:
                    msg_id = self.assistant_message_id
                    prior = self._last_text.get(msg_id or "", "")
                    new_text = prior + delta_str
                    if msg_id:
                        self._last_text[msg_id] = new_text
                    self.assistant_text = new_text
                    self._emit({
                        "kind": "token_delta",
                        "message_id": msg_id,
                        "delta": delta_str,
                        "channel": getattr(self, "assistant_channel", None) or "final",
                    }, output)
                elif op == "replace" and delta_str:
                    msg_id = self.assistant_message_id
                    prior = self._last_text.get(msg_id or "", "")
                    if delta_str.startswith(prior):
                        delta = delta_str[len(prior):]
                    else:
                        delta = delta_str
                    if msg_id:
                        self._last_text[msg_id] = delta_str
                    self.assistant_text = delta_str
                    if delta:
                        self._emit({
                            "kind": "token_delta",
                            "message_id": msg_id,
                            "delta": delta,
                            "channel": getattr(self, "assistant_channel", None) or "final",
                        }, output)

        message = event.get("message")
        if not isinstance(message, Mapping) and isinstance(v, Mapping):
            message = v.get("message")
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

        if role == "assistant" and channel:
            self.assistant_channel = channel

        parts = content.get("parts")
        text = ""
        if isinstance(parts, list):
            text = "".join(part for part in parts if isinstance(part, str))

        if channel == "analysis" or content_type in {"thoughts", "reasoning", "reasoning_recap"}:
            self._emit({
                "kind": "thinking",
                "message_id": message_id,
                "content_type": content_type or None,
                "thought_snippet": text[-160:] if text else None,
            }, output)

        metadata = message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}

        if (
            "tool" in content_type
            or (recipient and recipient not in {"all", "assistant", "user"})
            or content_type in {"computer_initialize_state", "computer_output"}
        ):
            target = ""
            if "url" in metadata:
                target = str(metadata["url"])
            elif "search_query" in metadata:
                target = f"query: {metadata['search_query']}"
            elif "command" in metadata:
                target = str(metadata["command"])
            elif text:
                target = text.strip()[:140].replace("\n", " ")

            self._emit({
                "kind": "tool_invocation",
                "message_id": message_id,
                "recipient": recipient or None,
                "content_type": content_type or None,
                "target": target or None,
            }, output)

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

        if role == "assistant" and status == "finished_successfully" and bool(message.get("end_turn")):
            self._terminal("finished_successfully", output)

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        text = self._utf8_decoder.decode(bytes(chunk), final=False) if isinstance(chunk, (bytes, bytearray)) else str(chunk)
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
