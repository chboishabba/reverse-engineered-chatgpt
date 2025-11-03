"""Interactive command line interface for ChatGPT sessions."""

from __future__ import annotations

import sys
from typing import Dict, Iterable, List, Optional

from .errors import InvalidSessionToken, TokenNotProvided
from .sync_chatgpt import SyncChatGPT, SyncConversation
from .utils import get_session_token

# Exit commands recognised by the CLI.
EXIT_COMMANDS = {"exit", "quit", "q"}

# Number of conversations to show per page when browsing history.
CONVERSATION_PAGE_SIZE = 10


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


def _print_conversation_page(items: List[Dict], offset: int) -> None:
    """Display the current page of conversation titles."""

    if not items:
        print("No conversations on this page.")
        return

    start = offset + 1
    end = offset + len(items)
    print(f"\nShowing conversations {start}-{end}:")
    for index, conversation in enumerate(items, start=1):
        title = conversation.get("title") or "(no title)"
        print(f"  {index}. {title}")


def _pick_conversation_id(chatgpt: SyncChatGPT) -> Optional[str]:
    """Interactively choose a conversation ID or return ``None`` for new."""

    offset = 0
    seen_ids = set()
    cached_conversations: List[Dict] = []
    current_page: List[Dict] = []
    needs_refresh = True

    print(
        "\nConversation selection commands: 'view', 'next', 'prev', "
        "'search <keyword>', a number to select, or press Enter for a new chat."
    )

    while True:
        if needs_refresh:
            page = chatgpt.list_conversations_page(offset, CONVERSATION_PAGE_SIZE)
            items = page.get("items", [])

            if not items:
                if offset == 0:
                    print("No saved conversations found.")
                    return None
                print("No conversations on this page.")
                offset = max(0, offset - CONVERSATION_PAGE_SIZE)
                needs_refresh = True
                continue

            current_page = items
            for conversation in items:
                conversation_id = conversation.get("id")
                if conversation_id and conversation_id not in seen_ids:
                    seen_ids.add(conversation_id)
                    cached_conversations.append(conversation)
            _print_conversation_page(current_page, offset)
            needs_refresh = False

        try:
            command = input(
                "Select conversation (view/next/prev/search/#/<id>/Enter for new): "
            ).strip()
        except EOFError:
            print("\nInput stream closed. Starting a new conversation.")
            return None

        if not command:
            return None

        normalized = command.lower()

        if normalized == "view":
            _print_conversation_page(current_page, offset)
            continue

        if normalized == "next":
            if len(current_page) < CONVERSATION_PAGE_SIZE:
                print("No next page.")
            else:
                offset += CONVERSATION_PAGE_SIZE
                needs_refresh = True
            continue

        if normalized == "prev":
            if offset == 0:
                print("Already at first page.")
            else:
                offset -= CONVERSATION_PAGE_SIZE
                needs_refresh = True
            continue

        if normalized.startswith("search"):
            parts = command.split(maxsplit=1)
            if len(parts) == 1 or not parts[1].strip():
                print("Please provide a keyword to search.")
                continue

            keyword = parts[1].strip().lower()
            matches = [
                conv
                for conv in cached_conversations
                if keyword in (conv.get("title") or "").lower()
            ]
            if not matches:
                print(f"No conversations matching '{parts[1].strip()}'.")
            else:
                print(f"Found {len(matches)} conversation(s):")
                for conv in matches:
                    title = conv.get("title") or "(no title)"
                    cid = conv.get("id") or ""
                    print(f"- {title} [{cid}]")
            continue

        if command.isdigit():
            selection = int(command)
            if 1 <= selection <= len(current_page):
                return current_page[selection - 1].get("id") or ""
            print("Invalid selection number.")
            continue

        matching_cached = next(
            (conv for conv in cached_conversations if conv.get("id") == command),
            None,
        )
        if matching_cached:
            return matching_cached.get("id") or ""

        # Assume the user entered an ID that wasn't cached yet.
        return command


def select_conversation(chatgpt: SyncChatGPT) -> SyncConversation:
    """Create or resume a conversation based on user input."""

    conversation_id = _pick_conversation_id(chatgpt)
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


def stream_response(chunks: Iterable[dict]) -> None:
    """Stream assistant chunks to stdout."""

    for chunk in chunks:
        content = chunk.get("content")
        if content:
            print(content, end="", flush=True)
    print()  # ensure a newline after the assistant response


def main() -> None:
    """Entry point for the interactive CLI."""

    token = obtain_session_token()

    with SyncChatGPT(session_token=token) as chatgpt:
        print("\nSession established. Type 'exit', 'quit', or 'q' to leave the chat.")
        conversation = select_conversation(chatgpt)

        while True:
            try:
                prompt = input("You> ")
            except EOFError:
                print("\nEOF received. Exiting chat.")
                break

            if prompt.strip().lower() in EXIT_COMMANDS:
                print("Goodbye!")
                break

            if not prompt.strip():
                continue

            try:
                stream_response(conversation.chat(prompt))
            except Exception as exc:  # noqa: BLE001
                print(f"Encountered an error while chatting: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(1)
