from re_gpt import SyncChatGPT

# consts
session_token = "__Secure-next-auth.session-token here"

# Create ChatGPT instance using the session token
with SyncChatGPT(session_token=session_token) as chatgpt:
    conversations = chatgpt.list_all_conversations()

    for idx, conv in enumerate(conversations):
        print(f"{idx}: {conv['title']}")

    selected = int(input("Select conversation number: "))
    conversation = chatgpt.get_conversation(conversations[selected]["id"])

    prompt = input("Enter your prompt: ")
    for message_chunk in conversation.chat(prompt):
        print(message_chunk["content"], flush=True, end="")
"""Select and resume an existing ChatGPT conversation.

This example lists all available conversations and lets the user choose one
to continue. It works with both synchronous and asynchronous clients.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Dict

from re_gpt import SyncChatGPT, AsyncChatGPT

# Replace with your own session token
SESSION_TOKEN = "__Secure-next-auth.session-token here"


def choose_conversation(conversations: List[Dict]) -> str:
    """Prompt the user to select a conversation from ``conversations``."""

    for idx, conv in enumerate(conversations, start=1):
        title = conv.get("title") or "(no title)"
        print(f"{idx}. {title}")

    choice = int(input("Select a conversation: "))
    return conversations[choice - 1]["id"]


def run_sync() -> None:
    with SyncChatGPT(session_token=SESSION_TOKEN) as chatgpt:
        conversations = chatgpt.list_all_conversations()
        conversation_id = choose_conversation(conversations)
        conversation = chatgpt.get_conversation(conversation_id)

        while True:
            prompt = input("user: ")
            for message in conversation.chat(prompt):
                print(message["content"], end="", flush=True)
            print()


async def run_async() -> None:
    async with AsyncChatGPT(session_token=SESSION_TOKEN) as chatgpt:
        conversations = await chatgpt.list_all_conversations()
        conversation_id = choose_conversation(conversations)
        conversation = chatgpt.get_conversation(conversation_id)

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
    args = parser.parse_args()

    if args.use_async:
        if sys.version_info >= (3, 8) and sys.platform.lower().startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_async())
    else:
        run_sync()


if __name__ == "__main__":
    main()

