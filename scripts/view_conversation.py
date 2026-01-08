#!/usr/bin/env python3
"""Export a slice of a ChatGPT conversation via the stored session."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from re_gpt.storage import (
    ConversationStorage,
    NullConversationStorage,
    extract_ordered_messages,
)
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.utils import TokenNotProvided, get_default_user_agent, get_session_token
from re_gpt.view_helpers import parse_lines_range


def _parse_timestamp(value: str) -> float:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("timestamp must not be empty")

    try:
        return float(trimmed)
    except ValueError:
        pass

    normalized = trimmed
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp '{value}': {exc}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _parse_line_bounds(args: argparse.Namespace) -> Tuple[Optional[int], Optional[int]]:
    if args.lines_range:
        parsed = parse_lines_range(args.lines_range)
        if parsed:
            start, end = parsed
            start_idx = start - 1
            end_idx = None if end is None else end - 1
            return start_idx, end_idx

    start_idx = None
    end_idx = None
    if args.lines_min is not None:
        start_idx = max(args.lines_min - 1, 0)
    if args.lines_max is not None:
        end_idx = max(args.lines_max - 1, 0)
    return start_idx, end_idx


def _filter_messages(
    messages: Iterable[dict],
    *,
    start_idx: Optional[int],
    end_idx: Optional[int],
    since_index: Optional[int],
    since_time: Optional[float],
) -> List[dict]:
    filtered: List[dict] = []
    for message in messages:
        raw_index = message.get("message_index")
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = 0

        if since_index is not None and index < since_index:
            continue
        if start_idx is not None and index < start_idx:
            continue
        if end_idx is not None and index > end_idx:
            continue

        create_time = message.get("create_time")
        try:
            timestamp = float(create_time)
        except (TypeError, ValueError):
            timestamp = None
        if since_time is not None and timestamp is not None and timestamp <= since_time:
            continue

        filtered.append(message)
    return filtered


def _select_conversation_id(
    chatgpt: SyncChatGPT, *, conversation_id: Optional[str], title: Optional[str]
) -> str:
    if conversation_id:
        return conversation_id

    if not title:
        raise ValueError("Either --conversation-id or --title must be provided.")

    catalog = chatgpt.list_all_conversations()
    lowered = title.lower()
    partial_matches = []
    for conv in catalog:
        candidate = (conv.get("title") or "").lower()
        if candidate == lowered:
            cid = conv.get("id")
            if cid:
                return cid
        if lowered in candidate:
            cid = conv.get("id")
            if cid:
                partial_matches.append(cid)

    if partial_matches:
        return partial_matches[0]

    raise ValueError(f"Conversation titled '{title}' not found.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--conversation-id",
        "--cID",
        dest="conversation_id",
        help="Explicit conversation ID to export.",
    )
    parser.add_argument("--title", help="Conversation title to look up when ID is unknown.")
    parser.add_argument(
        "--lines",
        dest="lines_range",
        help="Lines range expression like '1-5', '10+', or '42'.",
    )
    parser.add_argument("--lines-min", type=int, help="Start line number (1-indexed).")
    parser.add_argument("--lines-max", type=int, help="End line number (1-indexed).")
    parser.add_argument(
        "--since-last-update",
        action="store_true",
        help="Only include messages that were not previously persisted.",
    )
    parser.add_argument(
        "--since-time",
        help="Only include messages after the given timestamp (float or ISO-8601).",
    )
    parser.add_argument(
        "--token",
        "--key",
        "-k",
        dest="token",
        help="Session token (falls back to config.ini or ~/.chatgpt_session).",
    )
    parser.add_argument("--model", help="Model slug to use for the connection.")
    parser.add_argument(
        "--output",
        "-o",
        help="Path to write the filtered conversation (defaults to stdout).",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Force fetching the conversation via the backend API even if cached locally.",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Persist the fetched conversation to local storage.",
    )
    parser.add_argument(
        "--nostore",
        action="store_true",
        help="Do not write to the SQLite storage (uses cached metadata only).",
    )
    args = parser.parse_args()

    if args.store and args.nostore:
        parser.error("--store and --nostore cannot be combined.")

    if args.lines_min is not None and args.lines_min < 1:
        parser.error("--lines-min must be >= 1")
    if args.lines_max is not None and args.lines_max < 1:
        parser.error("--lines-max must be >= 1")
    if (
        args.lines_min is not None
        and args.lines_max is not None
        and args.lines_max < args.lines_min
    ):
        parser.error("--lines-max must be >= --lines-min")

    if not args.conversation_id and not args.title:
        parser.error("Provide --conversation-id/--cID or --title.")

    try:
        token = args.token or get_session_token()
    except TokenNotProvided as exc:
        parser.error(str(exc))

    try:
        since_time = _parse_timestamp(args.since_time) if args.since_time else None
    except ValueError as exc:
        parser.error(f"Invalid --since-time value: {exc}")

    start_idx, end_idx = _parse_line_bounds(args)
    storage_context = NullConversationStorage() if args.nostore else ConversationStorage()

    with storage_context as storage, SyncChatGPT(
        session_token=token,
        default_model=args.model,
        user_agent=get_default_user_agent(),
    ) as chatgpt:
        try:
            selected_id = _select_conversation_id(
                chatgpt,
                conversation_id=args.conversation_id,
                title=args.title,
            )
        except ValueError as exc:
            parser.error(str(exc))

        if args.remote:
            chat = chatgpt.fetch_conversation(selected_id)
        else:
            conversation = chatgpt.get_conversation(selected_id)
            chat = conversation.fetch_chat()
        messages = extract_ordered_messages(chat)

        since_index = (
            storage.count_messages(selected_id) if args.since_last_update else None
        )

        filtered = _filter_messages(
            messages,
            start_idx=start_idx,
            end_idx=end_idx,
            since_index=since_index,
            since_time=since_time,
        )

        if not filtered:
            print("No messages match the requested filters.")
            return

        lines = [
            f"{msg.get('author', 'unknown').capitalize()} [{int(msg.get('message_index', 0)) + 1}]: {msg.get('content', '')}"
            for msg in filtered
        ]

        output_path = args.output
        conversation_title = (
            conversation.title if not args.remote else chat.get("title") or "(no title)"
        )
        header = f"--- Conversation: {conversation_title} ({selected_id}) ---"
        if output_path:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(f"{header}\n")
                handle.write("\n".join(lines))
                handle.write("\n")
        else:
            print(header)
            print("\n".join(lines))
        if args.store and not args.nostore:
            storage.persist_chat(
                selected_id,
                chat,
                messages,
            )


if __name__ == "__main__":
    main()
