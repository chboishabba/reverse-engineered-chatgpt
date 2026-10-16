"""Canonical graph receipts for browser-owned mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptMutationReceipt:
    prompt_committed: bool
    user_node_ids: tuple[str, ...]
    assistant_text: str | None


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    texts = [part for part in parts if isinstance(part, str)]
    return "\n".join(texts).strip()


def _message_role(message: dict[str, Any]) -> str:
    author = message.get("author")
    if not isinstance(author, dict):
        return ""
    return str(author.get("role") or "").strip()


def _message_time(message: dict[str, Any]) -> float | None:
    for key in ("create_time", "update_time"):
        raw = message.get(key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def classify_prompt_mutation(
    chat: dict[str, Any],
    prompt: str,
    *,
    not_before: float,
) -> PromptMutationReceipt:
    """Find a newly-created exact user prompt and any assistant child.

    ``not_before`` prevents an old identical probe from certifying a failed new
    send. Quoted prompt text in assistant/tool messages is ignored because the
    role must be ``user`` and the message content must exactly match the prompt.
    """

    mapping = chat.get("mapping")
    if not isinstance(mapping, dict):
        return PromptMutationReceipt(False, (), None)

    expected = prompt.strip()
    matched_ids: list[str] = []
    matched_times: dict[str, float] = {}

    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        if _message_role(message) != "user":
            continue
        if _message_text(message) != expected:
            continue
        created = _message_time(message)
        if created is None or created < not_before:
            continue
        node_key = str(node_id)
        matched_ids.append(node_key)
        matched_times[node_key] = created

    if not matched_ids:
        return PromptMutationReceipt(False, (), None)

    matched_ids.sort(key=lambda node_id: matched_times.get(node_id, 0.0))
    selected_user = matched_ids[-1]

    assistant_candidates: list[tuple[float, str]] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict) or str(node.get("parent") or "") != selected_user:
            continue
        message = node.get("message")
        if not isinstance(message, dict) or _message_role(message) != "assistant":
            continue
        text = _message_text(message)
        if not text:
            continue
        created = _message_time(message) or 0.0
        assistant_candidates.append((created, text))

    assistant_text = None
    if assistant_candidates:
        assistant_candidates.sort(key=lambda item: item[0])
        assistant_text = assistant_candidates[-1][1]

    return PromptMutationReceipt(True, tuple(matched_ids), assistant_text)
