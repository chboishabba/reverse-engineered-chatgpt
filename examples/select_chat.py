from __future__ import annotations

"""Select and resume an existing ChatGPT conversation.

This example demonstrates how to page through existing conversations using
``list_conversations_page(offset, limit)``.  Use ``n`` for the next page,
``p`` for the previous page or enter the conversation number to continue.
Fetched metadata is written to ``conversations.json`` while only the current
page is printed.

After picking a conversation, its full history is downloaded and written to
``conversation_<id>.json``.  Messages are shown a page at a time (20 entries
per page) and can be navigated with ``n`` for next, ``p`` for previous and
``q`` to quit the viewer before resuming the chat.  The script works with both
synchronous and asynchronous clients.
"""

import argparse
import asyncio
import json
import sys
from typing import Dict, List, Optional

from re_gpt import AsyncChatGPT, SyncChatGPT
from re_gpt.utils import get_session_token

SESSION_TOKEN = get_session_token()

MESSAGE_PAGE_SIZE = 20


def _dump_conversations(conversations: List[Dict]) -> None:
    """Persist ``conversations`` to ``conversations.json``."""

    with open("conversations.json", "w", encoding="utf-8") as f:
        json.dump(conversations, f, indent=2)


def _extract_messages(chat: Dict) -> List[Dict]:
    """Return ordered messages from a conversation ``chat`` mapping."""

    messages = []
    for node in chat.get("mapping", {}).values():
        msg = node.get("message")
        if not msg:
            continue
        content_parts = msg.get("content", {}).get("parts") or []
        if not content_parts:
            continue

        normalized_parts: List[str] = []
        for part in content_parts:
            if isinstance(part, str):
                text = part.strip()
            elif isinstance(part, dict):
                text = str(
                    part.get("text")
                    or part.get("content")
                    or part.get("title")
                    or ""
                ).strip()
            else:
                text = ""

            if text:
                normalized_parts.append(text)

        if not normalized_parts:
            continue

        content = "\n".join(normalized_parts)

        # ``create_time`` is sometimes ``None`` for system messages; fallback to ``0``
        # so that sorting works and these messages appear first.
        messages.append(
            {
                "role": msg.get("author", {}).get("role", ""),
                "content": content,
                "create_time": msg.get("create_time") or 0,
            }
        )
    messages.sort(key=lambda m: m["create_time"])
    return messages


def _page_messages(messages: List[Dict]) -> None:
    """Display ``messages`` in pages and allow navigation commands."""

    offset = 0
    total = len(messages)
    while True:
        end = min(total, offset + MESSAGE_PAGE_SIZE)
        for msg in messages[offset:end]:
            print(f"{msg['role']}: {msg['content']}")
            print()

        cmd = input("Command (n/p/q): ").strip().lower()
        if cmd == "n":
            if end >= total:
                print("No next page.")
            else:
                offset += MESSAGE_PAGE_SIZE
        elif cmd == "p":
            if offset == 0:
                print("Already at first page.")
            else:
                offset -= MESSAGE_PAGE_SIZE
        elif cmd == "q":
            break


def choose_conversation_sync(chatgpt: SyncChatGPT, limit: int) -> Optional[str]:
    """Page through conversations using ``limit`` and return a chosen ID."""

    offset = 0
    all_conversations: List[Dict] = []
    seen_ids = set()

    while True:
        page = chatgpt.list_conversations_page(offset, limit)
        items = page.get("items", [])

        if not items and offset != 0:
            print("No conversations on this page.")
            offset = max(0, offset - limit)
            continue

        for conv in items:
            cid = conv["id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_conversations.append(conv)
        _dump_conversations(all_conversations)

        for idx, conv in enumerate(items, start=1):
            title = conv.get("title") or "(no title)"
            print(f"{idx}. {title}")

        cmd = input("Select conversation or command (n/p/q): ").strip().lower()
        if cmd == "n":
            if len(items) < limit:
                print("No next page.")
            else:
                offset += limit
        elif cmd == "p":
            if offset == 0:
                print("Already at first page.")
            else:
                offset -= limit
        elif cmd == "q":
            return None
        elif cmd.isdigit() and 1 <= int(cmd) <= len(items):
            return items[int(cmd) - 1]["id"]


async def choose_conversation_async(chatgpt: AsyncChatGPT, limit: int) -> Optional[str]:
    """Asynchronous version of :func:`choose_conversation_sync`."""

    offset = 0
    all_conversations: List[Dict] = []
    seen_ids = set()

    while True:
        page = await chatgpt.list_conversations_page(offset, limit)
        items = page.get("items", [])

        if not items and offset != 0:
            print("No conversations on this page.")
            offset = max(0, offset - limit)
            continue

        for conv in items:
            cid = conv["id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_conversations.append(conv)
        _dump_conversations(all_conversations)

        for idx, conv in enumerate(items, start=1):
            title = conv.get("title") or "(no title)"
            print(f"{idx}. {title}")

        cmd = input("Select conversation or command (n/p/q): ").strip().lower()
        if cmd == "n":
            if len(items) < limit:
                print("No next page.")
            else:
                offset += limit
        elif cmd == "p":
            if offset == 0:
                print("Already at first page.")
            else:
                offset -= limit
        elif cmd == "q":
            return None
        elif cmd.isdigit() and 1 <= int(cmd) <= len(items):
            return items[int(cmd) - 1]["id"]


def run_sync(limit: int) -> None:
    with SyncChatGPT(session_token=SESSION_TOKEN) as chatgpt:
        conversation_id = choose_conversation_sync(chatgpt, limit)
        if conversation_id is None:
            return
        conversation = chatgpt.get_conversation(conversation_id)
        chat = conversation.fetch_chat()
        with open(
            f"conversation_{conversation_id}.json", "w", encoding="utf-8"
        ) as f:
            json.dump(chat, f, indent=2)
        _page_messages(_extract_messages(chat))

        while True:
            prompt = input("user: ")
            for message in conversation.chat(prompt):
                print(message["content"], end="", flush=True)
            print()


async def run_async(limit: int) -> None:
    async with AsyncChatGPT(session_token=SESSION_TOKEN) as chatgpt:
        conversation_id = await choose_conversation_async(chatgpt, limit)
        if conversation_id is None:
            return
        conversation = chatgpt.get_conversation(conversation_id)
        chat = await conversation.fetch_chat()
        with open(
            f"conversation_{conversation_id}.json", "w", encoding="utf-8"
        ) as f:
            json.dump(chat, f, indent=2)
        _page_messages(_extract_messages(chat))

        while True:
            prompt = input("user: ")
            async for message in conversation.chat(prompt):
                print(message["content"], end="", flush=True)
            print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--async", action="store_true", dest="use_async", help="Use AsyncChatGPT"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of conversations per page",
    )
    args = parser.parse_args()

    if args.use_async:
        if sys.version_info >= (3, 8) and sys.platform.lower().startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_async(args.limit))
    else:
        run_sync(args.limit)


if __name__ == "__main__":
    main()

