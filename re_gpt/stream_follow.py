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
        self.assistant_channel: str | None = None
        self.current_content_type: str | None = None
        self.current_recipient: str | None = None
        self.assistant_text = ""
        self.thinking_text = ""
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
        if msg_type in {"conversation_completed"}:
            self._terminal(msg_type, output)

        message = event.get("message")
        if not isinstance(message, Mapping) and isinstance(v, Mapping):
            message = v.get("message")
        if isinstance(message, Mapping):
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

            if role == "assistant":
                if channel:
                    self.assistant_channel = channel
                if content_type:
                    self.current_content_type = content_type
                if recipient:
                    self.current_recipient = recipient

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
                is_thinking_block = (
                    channel == "analysis"
                    or content_type in {"thoughts", "reasoning", "reasoning_recap"}
                )
                is_tool_block = (
                    "tool" in content_type
                    or content_type in {"computer_initialize_state", "computer_output"}
                    or bool(recipient and recipient not in {"all", "assistant", "user", ""})
                )
                prior = self._last_text.get(message_id, "")
                if text.startswith(prior):
                    delta = text[len(prior):]
                else:
                    delta = text
                self._last_text[message_id] = text
                if is_thinking_block:
                    self.thinking_text = text
                    if delta:
                        self._emit({
                            "kind": "thinking",
                            "message_id": message_id,
                            "delta": delta,
                            "channel": channel or "analysis",
                        }, output)
                elif not is_tool_block:
                    self.assistant_text = text
                    if delta:
                        self._emit({
                            "kind": "token_delta",
                            "message_id": message_id,
                            "delta": delta,
                            "channel": channel or None,
                        }, output)

            status = str(message.get("status") or "").strip().lower()
            is_thinking_or_tool = (
                channel == "analysis"
                or content_type in {"thoughts", "reasoning", "reasoning_recap", "tool_call", "computer_output"}
                or bool(recipient and recipient not in {"all", "assistant", "user", ""})
            )
            if role == "assistant" and status == "finished_successfully" and bool(message.get("end_turn")):
                if not is_thinking_or_tool:
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

            is_thinking = (
                self.assistant_channel == "analysis"
                or self.current_content_type in {"thoughts", "reasoning", "reasoning_recap"}
            )
            is_tool = (
                self.current_content_type in {"tool_call", "computer_output"}
                or bool(self.current_recipient and self.current_recipient not in {"all", "assistant", "user", ""})
            )

            if p == "/message/status" and str(pval).lower() == "finished_successfully":
                if not is_thinking and not is_tool:
                    self._terminal("finished_successfully", output)
            elif p == "/message/end_turn" and bool(pval):
                if not is_thinking and not is_tool:
                    self._terminal("finished_successfully", output)
            elif p.startswith("/message/content/parts/") or p == "/message/content/parts":
                delta_str = str(pval) if pval is not None else ""
                msg_id = self.assistant_message_id
                prior = self._last_text.get(msg_id or "", "")
                if op == "append" and delta_str:
                    new_text = prior + delta_str
                elif op == "replace" and delta_str:
                    new_text = delta_str
                else:
                    new_text = prior

                if delta_str and new_text != prior:
                    delta = new_text[len(prior):] if new_text.startswith(prior) else new_text
                    if msg_id:
                        self._last_text[msg_id] = new_text
                    if is_thinking:
                        self.thinking_text = new_text
                        self._emit({
                            "kind": "thinking",
                            "message_id": msg_id,
                            "delta": delta,
                            "channel": self.assistant_channel or "analysis",
                        }, output)
                    else:
                        self.assistant_text = new_text
                        self._emit({
                            "kind": "token_delta",
                            "message_id": msg_id,
                            "delta": delta,
                            "channel": self.assistant_channel or "final",
                        }, output)

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
