"""Compatibility bridge from legacy ``SyncConversation.chat`` to MutationService.

The interactive CLI and older Python callers keep their generator interface,
but mutation ownership moves to the same API-first exactly-once service used by
MCP and the supported ``re-gpt`` command. The canonical graph is persisted by
MutationService; a narrow append-message guard prevents the legacy CLI from
adding duplicate optimistic rows immediately afterwards.
"""

from __future__ import annotations

import time
from typing import Any, Generator

from .mutation_service import MutationService
from .storage import ConversationStorage
from .sync_chatgpt import SyncConversation

_RECENT_CANONICAL_MESSAGES: dict[tuple[str, str, str], float] = {}
_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_APPEND_MESSAGE = None


def _service_for(conversation: Any) -> MutationService:
    chatgpt = conversation.chatgpt
    return MutationService(
        session_token=getattr(chatgpt, "session_token", None),
        model=getattr(conversation, "model", None) or getattr(chatgpt, "default_model", None),
    )


def _mark_canonical(conversation_id: str, author: str, content: str | None) -> None:
    text = str(content or "").strip()
    if conversation_id and text:
        _RECENT_CANONICAL_MESSAGES[(conversation_id, author, text)] = time.monotonic()


def _prune_recent() -> None:
    cutoff = time.monotonic() - 30.0
    stale = [key for key, when in _RECENT_CANONICAL_MESSAGES.items() if when < cutoff]
    for key in stale:
        _RECENT_CANONICAL_MESSAGES.pop(key, None)


def mutation_service_chat(
    conversation: Any,
    user_input: str,
    *,
    service: MutationService | Any | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Implement the legacy generator API using one canonical service mutation."""

    prompt = str(user_input or "").strip()
    if not prompt:
        raise ValueError("prompt is empty")
    mutation_service = service or _service_for(conversation)
    conversation_id = str(getattr(conversation, "conversation_id", None) or "").strip() or None

    if conversation_id:
        result = mutation_service.continue_thread(
            conversation_id,
            prompt,
            write_transport="async",
            dom_fallback=True,
            follow=True,
        )
    else:
        result = mutation_service.new_conversation(prompt, follow=True)

    state = str(result.get("state") or "commit_unknown")
    resolved_id = str(result.get("conversation_id") or conversation_id or "").strip() or None
    if state not in {"complete", "committed", "committed_pending"}:
        operation_id = str(result.get("operation_id") or "")
        raise RuntimeError(
            f"mutation operation {operation_id or '<unknown>'} is {state}; Do not resend automatically"
        )
    if not resolved_id:
        raise RuntimeError("canonical mutation has no conversation id; Do not resend automatically")

    conversation.conversation_id = resolved_id
    next_parent = result.get("assistant_node_id") or result.get("user_node_id")
    if next_parent:
        conversation.parent_id = str(next_parent)

    assistant_text = str(result.get("assistant_text") or "").strip()
    _prune_recent()
    _mark_canonical(resolved_id, "user", prompt)
    _mark_canonical(resolved_id, "assistant", assistant_text)

    if assistant_text:
        yield {
            "content": assistant_text,
            "conversation_id": resolved_id,
            "message_id": result.get("assistant_node_id"),
        }


def _compat_chat(self: SyncConversation, user_input: str):
    yield from mutation_service_chat(self, user_input)


def _deduplicating_append_message(
    self: ConversationStorage,
    conversation_id: str,
    author: str,
    content: str,
    create_time: float | None = None,
) -> int:
    _prune_recent()
    key = (str(conversation_id), str(author), str(content or "").strip())
    if key in _RECENT_CANONICAL_MESSAGES:
        _RECENT_CANONICAL_MESSAGES.pop(key, None)
        return max(0, self.count_messages(str(conversation_id)) - 1)
    assert _ORIGINAL_APPEND_MESSAGE is not None
    return _ORIGINAL_APPEND_MESSAGE(self, conversation_id, author, content, create_time)


def install_unified_sync_mutation_plane() -> None:
    """Install the compatibility shim exactly once for legacy sync callers."""

    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_APPEND_MESSAGE
    if _INSTALLED:
        return
    _ORIGINAL_CHAT = SyncConversation.chat
    _ORIGINAL_APPEND_MESSAGE = ConversationStorage.append_message
    SyncConversation.chat = _compat_chat  # type: ignore[method-assign]
    ConversationStorage.append_message = _deduplicating_append_message  # type: ignore[method-assign]
    _INSTALLED = True
