"""Canonical single-conversation read compatibility layer.

The supported CLI uses :meth:`SyncChatGPT.fetch_conversation` for exact
conversation reads.  Legacy ``SyncConversation.fetch_chat`` remains available
for callers that hold a conversation object, but delegates to the validated
parent fetcher instead of performing its own unchecked HTTP request.
"""

from __future__ import annotations

import functools
import uuid
from typing import Any

from .async_chatgpt import MODELS
from .normalized_artifact import write_conversation_source_artifact
from .storage import NullConversationStorage, extract_ordered_messages
from .sync_chatgpt import SyncConversation
from .utils import get_model_slug
from .view_helpers import normalize_conversation_selector, parse_view_argument


def is_conversation_uuid(value: str) -> bool:
    """Return whether *value* is exactly a UUID conversation selector."""

    value = normalize_conversation_selector((value or "").strip())
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return str(parsed).lower() == value.lower()


def _populate_legacy_conversation_state(conversation: SyncConversation, chat: dict[str, Any]) -> None:
    mapping = chat.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("conversation response has no non-empty mapping")

    conversation.parent_id = chat.get("current_node") or next(reversed(mapping))
    conversation.title = chat.get("title")
    model_slug = get_model_slug(chat)
    conversation.model = next(
        (key for key, value in MODELS.items() if value["slug"] == model_slug),
        model_slug,
    )


def validated_fetch_chat(conversation: SyncConversation) -> dict[str, Any]:
    """Compatibility implementation of ``SyncConversation.fetch_chat``."""

    if not conversation.conversation_id:
        return {}
    chat = conversation.chatgpt.fetch_conversation(conversation.conversation_id)
    _populate_legacy_conversation_state(conversation, chat)
    return chat


def _install_uuid_view_handler(cli_module) -> None:
    original = cli_module.run_noninteractive_view

    def run_noninteractive_view(argument, chatgpt, storage, since_last_override=False):
        target, lines_range, since_last_update = parse_view_argument(argument)
        since_last_update = since_last_update or since_last_override
        if not target or not is_conversation_uuid(target):
            return original(argument, chatgpt, storage, since_last_override)

        conversation_id = normalize_conversation_selector(target)
        try:
            chat = chatgpt.fetch_conversation(conversation_id)
        except Exception as exc:  # noqa: BLE001 - preserve the validated fetch error.
            print(f"Failed to fetch conversation {conversation_id}: {exc}")
            return

        messages = extract_ordered_messages(chat)
        since_index = None
        if since_last_update:
            if isinstance(storage, NullConversationStorage):
                print(
                    "Storage disabled; '--view ... since last update' requires conversation persistence."
                )
                return
            try:
                since_index = storage.count_messages(conversation_id)
            except Exception as exc:  # noqa: BLE001
                print(f"Unable to compute 'since last update': {exc}")
                return

        start_idx, end_idx = cli_module._line_range_indices(lines_range)
        filtered_messages = cli_module._filter_messages(
            messages, start_idx, end_idx, since_index
        )
        notice = cli_module._build_notice_message(
            filtered_messages, since_last_update, lines_range
        )
        if notice:
            print(notice)
        for line in cli_module._build_conversation_lines(
            chat.get("title"), conversation_id, filtered_messages
        ):
            print(line)

    cli_module.run_noninteractive_view = run_noninteractive_view


def _install_uuid_download_handler(cli_module) -> None:
    original = cli_module.handle_download_command

    def handle_download_command(
        user_input,
        chatgpt,
        storage,
        current_page=None,
        cached_conversations=None,
        since_last_update=False,
        normalized_artifact_out=None,
    ):
        parts = user_input.strip().split(maxsplit=1)
        if len(parts) < 2:
            return original(
                user_input,
                chatgpt,
                storage,
                current_page=current_page,
                cached_conversations=cached_conversations,
                since_last_update=since_last_update,
                normalized_artifact_out=normalized_artifact_out,
            )

        selector = normalize_conversation_selector(parts[1])
        if not is_conversation_uuid(selector):
            return original(
                user_input,
                chatgpt,
                storage,
                current_page=current_page,
                cached_conversations=cached_conversations,
                since_last_update=since_last_update,
                normalized_artifact_out=normalized_artifact_out,
            )

        conversation_id = selector
        if since_last_update:
            if isinstance(storage, NullConversationStorage):
                print("Storage disabled; '--since-last' requires conversation persistence.")
                return
            if not cli_module._should_download_since_last(conversation_id, storage):
                print("No conversations have updates since the last download.")
                return

        try:
            chat = chatgpt.fetch_conversation(conversation_id)
        except Exception as exc:  # noqa: BLE001 - report the exact validated-read failure.
            print(f"Failed to fetch conversation {conversation_id}: {exc}")
            return

        messages = extract_ordered_messages(chat)
        asset_fetcher = None
        if hasattr(chatgpt, "download_asset"):
            asset_fetcher = functools.partial(
                chatgpt.download_asset, conversation_id=conversation_id
            )
        result = storage.persist_chat(
            conversation_id,
            chat,
            messages,
            asset_fetcher=asset_fetcher,
        )

        status = (
            f"+{result.new_messages} new message(s)"
            if result.new_messages
            else "no new messages"
        )
        asset_bits = []
        if result.asset_paths:
            asset_bits.append(f"saved {len(result.asset_paths)} image(s)")
        if result.asset_errors:
            asset_bits.append(f"{len(result.asset_errors)} image(s) failed")
        asset_info = "" if not asset_bits else " | " + ", ".join(asset_bits)
        path_info = (
            f" to {result.json_path}"
            if result.json_path
            else " to SQLite cache only"
        )
        print(
            f"Saved conversation {conversation_id} ({status}, cached "
            f"{result.total_messages}){asset_info}{path_info}"
        )

        if normalized_artifact_out:
            write_conversation_source_artifact(
                normalized_artifact_out,
                conversation_id=conversation_id,
                title=chat.get("title"),
                json_path=str(result.json_path) if result.json_path else None,
                remote_update_time=chat.get("update_time"),
                total_messages=result.total_messages,
                new_messages=result.new_messages,
                asset_count=len(result.asset_paths),
            )
            print(f"  Normalized artifact: {normalized_artifact_out}")

    cli_module.handle_download_command = handle_download_command


def install_validated_sync_read_plane() -> None:
    """Install validated sync reads across the supported legacy CLI seams."""

    if getattr(SyncConversation.fetch_chat, "_re_gpt_validated", False):
        return

    validated_fetch_chat._re_gpt_validated = True  # type: ignore[attr-defined]
    SyncConversation.fetch_chat = validated_fetch_chat  # type: ignore[method-assign]

    # Import lazily to avoid a module cycle: cli imports SyncConversation.
    from . import cli as cli_module

    _install_uuid_view_handler(cli_module)
    _install_uuid_download_handler(cli_module)
