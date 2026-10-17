"""Fragment-safe, content-redacting identity extraction for ChatGPT SSE streams."""

from __future__ import annotations

import json
from typing import Mapping, Sequence


def extract_graph_sse_identity(chunks: Sequence[bytes | str]) -> dict[str, str | None]:
    """Extract only conversation/user/assistant IDs from arbitrarily chunked SSE."""

    stream = "".join(
        chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for chunk in chunks
    )
    conversation_id: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    for raw_line in stream.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            event = json.loads(body)
        except Exception:
            continue
        if not isinstance(event, Mapping):
            continue
        if event.get("conversation_id"):
            conversation_id = str(event["conversation_id"])
        message = event.get("message")
        if not isinstance(message, Mapping) or not message.get("id"):
            continue
        author = message.get("author")
        role = str(author.get("role") or "") if isinstance(author, Mapping) else ""
        if role == "user":
            user_message_id = str(message["id"])
        elif role == "assistant":
            assistant_message_id = str(message["id"])
    return {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }
