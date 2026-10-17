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


def find_turn_assistant_nodes(
    mapping: dict[str, Any],
    user_node_id: str,
) -> list[tuple[int, float, str, str]]:
    """Return ``(has_text, created, node_id, text)`` for assistant nodes in ``user_node_id``'s turn.

    Reasoning models (e.g. ``gpt-6-thinking``) and tool-using turns insert
    intermediate ``thoughts``/``reasoning_recap``/``tool`` nodes between the user
    node and the final visible assistant text node. We traverse the descendant
    subtree rooted at ``user_node_id`` without crossing into any subsequent
    ``user`` turn.
    """
    children_by_parent: dict[str, list[str]] = {}
    for raw_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        nid = str(raw_id)
        parent = str(node.get("parent") or "").strip()
        if parent:
            children_by_parent.setdefault(parent, []).append(nid)
        for child in node.get("children") or ():
            cid = str(child).strip()
            if cid:
                children_by_parent.setdefault(nid, []).append(cid)

    queue: list[tuple[str, int]] = [(str(user_node_id), 0)]
    visited: set[str] = {str(user_node_id)}
    results: list[tuple[int, float, str, str]] = []

    while queue:
        current_id, depth = queue.pop(0)
        for child_id in children_by_parent.get(current_id, ()):
            if child_id in visited:
                continue
            visited.add(child_id)
            child_node = mapping.get(child_id)
            if not isinstance(child_node, dict):
                continue
            message = child_node.get("message")
            role = _message_role(message) if isinstance(message, dict) else ""
            if role == "user":
                continue
            if isinstance(message, dict) and role == "assistant":
                text = _message_text(message)
                created = _message_time(message) or float(depth)
                results.append((1 if text else 0, created, child_id, text))
            queue.append((child_id, depth + 1))

    results.sort(key=lambda item: (item[0], item[1]))
    return results


def classify_prompt_mutation(
    chat: dict[str, Any],
    prompt: str,
    *,
    not_before: float,
) -> PromptMutationReceipt:
    """Find a newly-created exact user prompt and any assistant reply in that turn.

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

    assistant_nodes = find_turn_assistant_nodes(mapping, selected_user)
    text_candidates = [text for has_text, _created, _nid, text in assistant_nodes if has_text and text]
    assistant_text = text_candidates[-1] if text_candidates else None

    return PromptMutationReceipt(True, tuple(matched_ids), assistant_text)
