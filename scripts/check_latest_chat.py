#!/usr/bin/env python3
"""Print the most recently updated chat and its latest message."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from re_gpt.errors import InvalidSessionToken, TokenNotProvided
from re_gpt.storage import extract_ordered_messages
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.utils import get_session_token


def _coerce_timestamp(value: Optional[object]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return float(trimmed)
        except ValueError:
            pass
        normalized = trimmed[:-1] + "+00:00" if trimmed.endswith("Z") else trimmed
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _format_timestamp(value: Optional[object]) -> str:
    coerced = _coerce_timestamp(value)
    if not coerced:
        return "unknown"
    return datetime.fromtimestamp(coerced, tz=timezone.utc).isoformat()


def _pick_most_recent(chatgpt: SyncChatGPT) -> Optional[Dict[str, Any]]:
    page = chatgpt.list_conversations_page(offset=0, limit=1)
    items = page.get("items", [])
    return items[0] if items else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the most recent conversation and print its latest message."
    )
    parser.add_argument(
        "--token",
        help="Session token value (defaults to config.ini or ~/.chatgpt_session).",
    )
    parser.add_argument(
        "--conversation-id",
        help="Conversation ID to fetch instead of the most recent chat.",
    )
    args = parser.parse_args()

    try:
        token = args.token or get_session_token()
    except TokenNotProvided:
        print("No session token found. Provide --token or add config.ini/~/.chatgpt_session.")
        return 2

    try:
        with SyncChatGPT(session_token=token) as chatgpt:
            if not getattr(chatgpt, "auth_token", None):
                raise InvalidSessionToken

            if args.conversation_id:
                conversation_id = args.conversation_id
                title = None
                update_time = None
            else:
                latest = _pick_most_recent(chatgpt)
                if not latest:
                    print("No conversations returned from the API.")
                    return 1
                conversation_id = latest.get("id")
                title = latest.get("title")
                update_time = latest.get("update_time")

            if not conversation_id:
                print("Unable to determine conversation id.")
                return 1

            conversation = chatgpt.get_conversation(conversation_id, title=title)
            chat = conversation.fetch_chat()
            messages = extract_ordered_messages(chat)

            last_message = messages[-1] if messages else {}
            author = last_message.get("author", "unknown")
            content = last_message.get("content", "")

            print(f"Conversation: {conversation.title or '(no title)'}")
            print(f"Conversation ID: {conversation_id}")
            if update_time is not None:
                print(f"Last updated (UTC): {_format_timestamp(update_time)}")
            print(f"Last author: {author}")
            print("Last message:")
            print(content)
    except InvalidSessionToken:
        print("Session token rejected. Please refresh __Secure-next-auth.session-token.")
        return 2
    except Exception as exc:
        print(f"Failed to fetch latest chat: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
