"""Conservative conversion of observed SSE progress into TUI activity.

Never invent unavailable private chain-of-thought. Tool arguments/results are
retained only when actually provided, and secret-looking keys are masked.
"""
from __future__ import annotations

from collections.abc import Mapping

SENSITIVE = ("token", "secret", "password", "cookie", "authorization", "api_key", "session", "credential")


def redact(value):
    if isinstance(value, Mapping):
        return {str(k): ("[redacted]" if any(word in str(k).lower() for word in SENSITIVE) else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def activity_from_progress(event):
    kind = str(event.get("kind") or "")
    if kind not in {"thinking", "tool_invocation", "tool_result", "reasoning_summary"}:
        return None
    recipient = str(event.get("recipient") or event.get("content_type") or "tool")
    summary = {
        "thinking": "Reasoning in progress (private details unavailable)",
        "reasoning_summary": "Reasoning summary available",
        "tool_invocation": "Tool: " + recipient[:120],
        "tool_result": "Tool result: " + recipient[:120],
    }[kind]
    return {"kind": kind, "summary": summary, "message_id": event.get("message_id"),
            "arguments": redact(event["arguments"]) if "arguments" in event else None,
            "result": redact(event["result"]) if "result" in event else None,
            "body": redact(event["body"]) if "body" in event else None}
