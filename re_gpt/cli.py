"""Interactive command line interface for ChatGPT sessions."""

from __future__ import annotations

import sys
import time
from typing import Iterable, Optional

from .errors import InvalidSessionToken, TokenNotProvided
from .storage import ConversationStorage, extract_ordered_messages
from .sync_chatgpt import SyncChatGPT, SyncConversation
from .utils import get_session_token

# Exit commands recognised by the CLI.
EXIT_COMMANDS = {"exit", "quit", "q"}


def print_token_instructions() -> None:
    """Print step-by-step instructions for locating the session token."""

    instructions = [
        "Open https://chatgpt.com/ in your browser and sign in.",
        "Open the developer tools (F12 or Cmd+Opt+I on macOS).",
        "Switch to the Application/Storage tab and expand Cookies.",
        "Select https://chatgpt.com and copy the value of ``__Secure-next-auth.session-token``.",
    ]
    print("\nHow to find your ChatGPT session token:")
    for index, step in enumerate(instructions, start=1):
        print(f"  {index}. {step}")
    print(
        "Once copied, paste the token below. Leave the input empty to reuse the "
        "value from config.ini or ~/.chatgpt_session.\n"
    )


def verify_session_token(token: str) -> None:
    """Ensure *token* is accepted by ChatGPT."""

    with SyncChatGPT(session_token=token):
        # Entering and leaving the context validates the token by fetching an
        # auth session.  No further action is required here.
        pass


def obtain_session_token() -> str:
    """Loop until a valid session token is provided.

    The function first tries any cached token discoverable via
    :func:`get_session_token`.  If that fails, the user is guided through
    copying the cookie value from the browser.
    """

    cached_token: Optional[str]
    try:
        cached_token = get_session_token()
    except TokenNotProvided:
        cached_token = None

    if cached_token:
        print("Attempting cached session token ...", flush=True)
        try:
            verify_session_token(cached_token)
            return cached_token
        except InvalidSessionToken:
            print(
                "The cached token was rejected. Please grab a fresh "
                "`__Secure-next-auth.session-token`."
            )
        except TokenNotProvided:
            print("The cached token was empty. You'll need to paste a new one.")

    print_token_instructions()

    while True:
        try:
            user_input = input(
                "Paste session token (press Enter to reuse stored token): "
            ).strip()
        except EOFError:
            print("\nInput stream closed. Exiting.")
            raise SystemExit(1) from None

        if not user_input:
            try:
                user_input = get_session_token()
                print("Using token found in config.ini or ~/.chatgpt_session.")
            except TokenNotProvided:
                print("No stored token available. Please paste the value manually.\n")
                continue

        try:
            verify_session_token(user_input)
            return user_input
        except TokenNotProvided:
            print("An empty token was provided. Please try again.\n")
        except InvalidSessionToken:
            print(
                "ChatGPT rejected the token. Ensure you copied the entire "
                "`__Secure-next-auth.session-token` value and try again.\n"
            )


def select_conversation(chatgpt: SyncChatGPT) -> SyncConversation:
    """Create or resume a conversation based on user input."""

    conversation_id = input(
        "Enter a conversation ID to resume (leave empty for a new chat): "
    ).strip()
    if conversation_id:
        conversation = chatgpt.get_conversation(conversation_id)
        try:
            conversation.fetch_chat()
            print(f"Resumed conversation {conversation_id}.")
            return conversation
        except Exception as exc:  # noqa: BLE001 - present friendly message.
            print(
                f"Unable to fetch conversation {conversation_id}: {exc}. "
                "Starting a new conversation instead."
            )
    conversation = chatgpt.create_new_conversation()
    print("Started a new conversation.")
    return conversation


def stream_response(chunks: Iterable[dict]) -> str:
    """Stream assistant chunks to stdout and return the assembled reply."""

    parts: list[str] = []
    for chunk in chunks:
        content = chunk.get("content")
        if content:
            print(content, end="", flush=True)
            parts.append(content)
    print()  # ensure a newline after the assistant response
    return "".join(parts)


def handle_download_command(
    user_input: str, chatgpt: SyncChatGPT, storage: ConversationStorage
) -> None:
    """Download and persist conversations based on ``user_input``."""

    parts = user_input.strip().split()
    if len(parts) == 1:
        print("Usage: download <conversation_id|all>")
        return

    targets: list[str]
    if parts[1].lower() == "all":
        conversations = chatgpt.list_all_conversations()
        targets = [conv.get("id") for conv in conversations if conv.get("id")]
        if not targets:
            print("No conversations available to download.")
            return
        print(f"Downloading {len(targets)} conversation(s)...")
    else:
        targets = [parts[1]]

    for conversation_id in targets:
        conversation = chatgpt.get_conversation(conversation_id)
        try:
            chat = conversation.fetch_chat()
        except Exception as exc:  # noqa: BLE001 - user-friendly output.
            print(f"Failed to fetch conversation {conversation_id}: {exc}")
            continue

        messages = extract_ordered_messages(chat)
        json_path = storage.persist_chat(conversation_id, chat, messages)
        print(
            "Saved conversation {cid} (messages: {count}) to {path}".format(
                cid=conversation_id,
                count=len(messages),
                path=json_path,
            )
        )


def main() -> None:
    """Entry point for the interactive CLI."""

    token = obtain_session_token()

    with ConversationStorage() as storage, SyncChatGPT(session_token=token) as chatgpt:
        print("\nSession established. Type 'exit', 'quit', or 'q' to leave the chat.")
        print("Use 'download <conversation_id>' or 'download all' to export chats.")
        conversation = select_conversation(chatgpt)

        while True:
            try:
                prompt = input("You> ")
            except EOFError:
                print("\nEOF received. Exiting chat.")
                break

            stripped_prompt = prompt.strip()
            lowered_prompt = stripped_prompt.lower()

            if lowered_prompt in EXIT_COMMANDS:
                print("Goodbye!")
                break

            if lowered_prompt.startswith("download"):
                handle_download_command(stripped_prompt, chatgpt, storage)
                continue

            if not stripped_prompt:
                continue

            try:
                response = stream_response(conversation.chat(prompt))
                conversation_id = conversation.conversation_id
                if conversation_id:
                    storage.append_message(
                        conversation_id,
                        author="user",
                        content=stripped_prompt,
                        create_time=time.time(),
                    )
                    if response:
                        storage.append_message(
                            conversation_id,
                            author="assistant",
                            content=response.strip(),
                            create_time=time.time(),
                        )
            except Exception as exc:  # noqa: BLE001
                print(f"Encountered an error while chatting: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(1)
