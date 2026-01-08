"""Interactive command line interface for ChatGPT sessions."""

from __future__ import annotations

import argparse
import functools
import subprocess
import shutil
import sys
import tempfile
import time
from typing import Dict, Iterable, List, Optional, Tuple

from .view_helpers import parse_view_argument

from .errors import InvalidSessionToken, TokenNotProvided, UnexpectedResponseError
from .storage import (
    ConversationStorage,
    NullConversationStorage,
    extract_ordered_messages,
)
from .sync_chatgpt import SyncChatGPT, SyncConversation
from .utils import (
    get_default_model,
    get_default_user_agent,
    get_model_slug,
    get_session_token,
)

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

    try:
        with SyncChatGPT(session_token=token) as chatgpt:
            # If the context manager succeeds but no auth token is present, treat as invalid.
            if not getattr(chatgpt, "auth_token", None):
                raise InvalidSessionToken
    except (InvalidSessionToken, TokenNotProvided):
        raise
    except Exception as exc:
        # Normalise unexpected failures during verification to InvalidSessionToken so the caller can prompt again.
        raise InvalidSessionToken from exc


def is_token_expired_error(exc: UnexpectedResponseError) -> bool:
    """Return ``True`` if *exc* represents an expired authentication token."""

    def _contains_expired_marker(payload: str) -> bool:
        if not payload:
            return False
        lowered = payload.lower()
        return "token_expired" in lowered or "authentication token is expired" in lowered

    current = exc
    while isinstance(current, UnexpectedResponseError):
        if _contains_expired_marker(getattr(current, "server_response", "")):
            return True
        original = getattr(current, "original_exception", None)
        if not isinstance(original, UnexpectedResponseError):
            break
        current = original

    return _contains_expired_marker(str(exc))


def obtain_session_token(key: Optional[str] = None) -> str:
    """Loop until a valid session token is provided.

    The function first tries any cached token discoverable via
    :func:`get_session_token`.  If that fails, the user is guided through
    copying the cookie value from the browser.
    """
    if key:
        return key
        
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


def handle_view_command(
    argument: str,
    chatgpt: SyncChatGPT,
    current_page: List[Dict],
    cached_conversations: List[Dict],
    storage: Optional[ConversationStorage] = None,
) -> None:
    """Handle the 'view' command to print a conversation's content."""
    if not argument:
        print(
            "Usage: view <conversation_number|conversation_id|title> "
            "[lines START[-END]|since last update]"
        )
        return

    target_argument, lines_range, since_last_update = parse_view_argument(argument)
    if not target_argument:
        print(
            "Usage: view <conversation_number|conversation_id|title> "
            "[lines START[-END]|since last update]"
        )
        return

    argument = target_argument
    conversation_id = ""
    if argument.isdigit():
        selection = int(argument)
        if 1 <= selection <= len(current_page):
            conversation_id = current_page[selection - 1].get("id")
        else:
            print("Invalid selection number.")
            return
    else:
        # Match by title first
        lowered_argument = argument.lower()
        matching_cached_by_title = next(
            (
                conv
                for conv in cached_conversations
                if (conv.get("title") or "").lower() == lowered_argument
            ),
            None,
        )
        if matching_cached_by_title:
            conversation_id = matching_cached_by_title.get("id")
        else:
            # Then match by ID
            matching_cached_by_id = next(
                (conv for conv in cached_conversations if conv.get("id") == argument),
                None,
            )
            if matching_cached_by_id:
                conversation_id = matching_cached_by_id.get("id")
            else:
                print(f"Conversation '{argument}' not found.")
                return

    if not conversation_id:
        print(f"Conversation '{argument}' not found.")
        return

    conversation_title = None
    # Try to find the title from cached conversations if available
    for conv in cached_conversations:
        if conv.get("id") == conversation_id:
            conversation_title = conv.get("title")
            break

    try:
        conversation = chatgpt.get_conversation(conversation_id, title=conversation_title)
        chat = conversation.fetch_chat()
        messages = extract_ordered_messages(chat)
        since_index: Optional[int] = None
        if since_last_update:
            if storage is None:
                print(
                    "Storage is required to determine which messages were "
                    "already persisted. Download the conversation first."
                )
                return
            try:
                since_index = storage.count_messages(conversation_id)
            except Exception as exc:  # noqa: BLE001
                print(f"Unable to compute 'since last update': {exc}")
                return

        start_idx: Optional[int] = None
        end_idx: Optional[int] = None
        if lines_range:
            start_idx = lines_range[0] - 1
            end_idx = None if lines_range[1] is None else lines_range[1] - 1

        filtered_messages: List[Dict] = []
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
            filtered_messages.append(message)

        notice_message = ""
        if not filtered_messages:
            if since_last_update:
                notice_message = "No new messages since the last update."
            elif lines_range:
                notice_message = "No messages match the requested range."
        if notice_message:
            print(notice_message)

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp_file:
            tmp_file.write(f"--- Conversation: {conversation.title} ({conversation_id}) ---\n")
            for message in filtered_messages:
                author = message.get("author", "unknown")
                content = message.get("content", "")
                try:
                    index = int(message.get("message_index"))
                except (TypeError, ValueError):
                    index = 0
                tmp_file.write(f"{author.capitalize()} [{index + 1}]: {content}\n")
            tmp_file.write("--- End of conversation ---\n")
            tmp_file_path = tmp_file.name

        pager = "less"
        if not shutil.which(pager):
            pager = "more"
        if not shutil.which(pager):
            pager = "cat"

        subprocess.run([pager, tmp_file_path])

    except Exception as exc:
        print(f"Failed to fetch conversation {conversation_id}: {exc}")
    finally:
        if 'tmp_file_path' in locals() and tmp_file_path:
            import os
            os.remove(tmp_file_path)


def _pick_conversation_id(chatgpt: SyncChatGPT, storage: ConversationStorage) -> Optional[Dict]:
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
            storage.record_conversations(items)
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

        parts = command.split(maxsplit=1)
        action = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if action == "download":
            handle_download_command(
                command,
                chatgpt,
                storage,
                current_page=current_page,
                cached_conversations=cached_conversations,
            )
            needs_refresh = True
            continue

        if action == "view":
            handle_view_command(
                argument,
                chatgpt,
                current_page,
                cached_conversations,
                storage,
            )
            continue
        elif action == "next":
            if len(current_page) < CONVERSATION_PAGE_SIZE:
                print("No next page.")
            else:
                offset += CONVERSATION_PAGE_SIZE
                needs_refresh = True
            continue
        elif action == "prev":
            if offset == 0:
                print("Already at first page.")
            else:
                offset -= CONVERSATION_PAGE_SIZE
                needs_refresh = True
            continue
        elif action == "search":
            if not argument:
                print("Please provide a keyword to search.")
                continue

            keyword = argument.lower()
            matches = [
                conv
                for conv in cached_conversations
                if keyword in (conv.get("title") or "").lower()
            ]
            if not matches:
                storage_matches = storage.search_conversations(argument)
                if storage_matches:
                    existing_ids = {conv.get("id") for conv in cached_conversations}
                    for conv in storage_matches:
                        cid = conv.get("id")
                        if cid and cid not in existing_ids:
                            cached_conversations.append(conv)
                            existing_ids.add(cid)
                    matches = storage_matches

            if not matches:
                print(f"No conversations matching '{argument}'.")
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
                selected_conv = current_page[selection - 1]
                return {"id": selected_conv.get("id"), "title": selected_conv.get("title")}
            print("Invalid selection number.")
            continue

        # Match by title first
        lowered_command = command.lower()
        matching_cached_by_title = next(
            (
                conv
                for conv in cached_conversations
                if (conv.get("title") or "").lower() == lowered_command
            ),
            None,
        )
        if matching_cached_by_title:
            return {"id": matching_cached_by_title.get("id"), "title": matching_cached_by_title.get("title")}

        # Then match by ID
        matching_cached_by_id = next(
            (conv for conv in cached_conversations if conv.get("id") == command),
            None,
        )
        if matching_cached_by_id:
            return {"id": matching_cached_by_id.get("id"), "title": matching_cached_by_id.get("title")}

        # Assume the user entered an ID that wasn't cached yet.
        return {"id": command, "title": None}


def select_conversation(chatgpt: SyncChatGPT, storage: ConversationStorage) -> SyncConversation:
    """Create or resume a conversation based on user input."""

    conversation_info = _pick_conversation_id(chatgpt, storage)
    if conversation_info:
        conversation_id = conversation_info.get("id")
        conversation_title = conversation_info.get("title")
        conversation = chatgpt.get_conversation(conversation_id, title=conversation_title)
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
    user_input: str,
    chatgpt: SyncChatGPT,
    storage: ConversationStorage,
    current_page: Optional[List[Dict]] = None,
    cached_conversations: Optional[List[Dict]] = None,
) -> None:
    """Download and persist conversations based on ``user_input``."""

    parts = user_input.strip().split(maxsplit=1)
    if len(parts) < 2:
        print("Usage: download <conversation_id|title|all|list>")
        return

    arg = parts[1]
    lowered_arg = arg.lower()
    targets: list[str] = []
    conversation_catalog: Optional[List[Dict]] = None

    if lowered_arg == "list":
        conversation_catalog = chatgpt.list_all_conversations()
        stats = storage.record_conversations(conversation_catalog)
        total = len(conversation_catalog)
        print(
            "Catalogued {total} conversation(s) "
            "(added {added}, refreshed {updated}).".format(
                total=total,
                added=stats.added,
                updated=stats.updated,
            )
        )
        return

    if lowered_arg == "all":
        conversation_catalog = chatgpt.list_all_conversations()
        stats = storage.record_conversations(conversation_catalog)
        targets = [conv.get("id") for conv in conversation_catalog if conv.get("id")]
        if not targets:
            print("No conversations available to download.")
            return
        print(
            "Downloading {count} conversation(s)... (added {added}, refreshed {updated})".format(
                count=len(targets),
                added=stats.added,
                updated=stats.updated,
            )
        )
    elif arg.isdigit() and current_page:
        selection = int(arg)
        if 1 <= selection <= len(current_page):
            conversation_id = current_page[selection - 1].get("id")
            if conversation_id:
                targets.append(conversation_id)
        else:
            print("Invalid selection number.")
            return

    if not targets:
        found = False

        collections: List[List[Dict]] = []
        if cached_conversations:
            collections.append(cached_conversations)
        if conversation_catalog:
            collections.append(conversation_catalog)

        for collection in collections:
            for conv in collection:
                cid = conv.get("id")
                title = (conv.get("title") or "").lower()
                if cid == arg or title == lowered_arg:
                    if cid:
                        targets.append(cid)
                        found = True
                        break
            if found:
                break

        if not found:
            fallback_catalog = chatgpt.list_all_conversations()
            storage.record_conversations(fallback_catalog)
            for conv in fallback_catalog:
                cid = conv.get("id")
                title = (conv.get("title") or "").lower()
                if cid == arg or title == lowered_arg:
                    if cid:
                        targets.append(cid)
                        found = True
                        break

        if not found:
            print(f"Conversation '{arg}' not found.")
            return

    for conversation_id in targets:
        conversation = chatgpt.get_conversation(conversation_id)
        try:
            chat = conversation.fetch_chat()
        except Exception as exc:  # noqa: BLE001 - user-friendly output.
            print(f"Failed to fetch conversation {conversation_id}: {exc}")
            continue

        messages = extract_ordered_messages(chat)
        asset_fetcher = None
        if hasattr(chatgpt, "download_asset"):
            asset_fetcher = functools.partial(
                chatgpt.download_asset,
                conversation_id=conversation_id,
            )

        result = storage.persist_chat(
            conversation_id,
            chat,
            messages,
            asset_fetcher=asset_fetcher,
        )
        if result.new_messages:
            status = f"+{result.new_messages} new message(s)"
        else:
            status = "no new messages"
        asset_bits: list[str] = []
        asset_count = len(result.asset_paths)
        failure_count = len(result.asset_errors)
        if asset_count:
            asset_bits.append(f"saved {asset_count} image(s)")
        if failure_count:
            asset_bits.append(f"{failure_count} image(s) failed")
        asset_info = ""
        if asset_bits:
            asset_info = " | " + ", ".join(asset_bits)
        print(
            "Saved conversation {cid} ({status}, cached {count}){assets} to {path}".format(
                cid=conversation_id,
                status=status,
                count=result.total_messages,
                assets=asset_info,
                path=result.json_path,
            )
        )


def main() -> None:
    """Entry point for the interactive CLI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", "-k", type=str, help="Session token")
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        help="Default model slug for new conversations (overrides config/env).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print conversation IDs and titles to stdout (use redirection for `rg`).",
    )
    parser.add_argument(
        "--nostore",
        action="store_true",
        help="Do not persist conversations or append messages to SQLite.",
    )
    args = parser.parse_args()
    token = obtain_session_token(args.key)
    default_model = args.model or get_default_model()
    user_agent = get_default_user_agent()

    storage_context = NullConversationStorage() if args.nostore else ConversationStorage()

    with storage_context as storage, SyncChatGPT(
        session_token=token, default_model=default_model, user_agent=user_agent
    ) as chatgpt:
        if args.nostore:
            print("Storage disabled; conversations will not be saved locally.")
        if args.list:
            catalog = chatgpt.list_all_conversations()
            storage.record_conversations(catalog)
            for conv in catalog:
                cid = conv.get("id") or ""
                title = conv.get("title") or "(no title)"
                print(f"{cid}\t{title}")
            return
        detected_model = None
        try:
            page = chatgpt.list_conversations_page(offset=0, limit=1)
            items = page.get("items", [])
            if items:
                conversation_id = items[0].get("id")
                if conversation_id:
                    conversation = chatgpt.get_conversation(conversation_id)
                    chat = conversation.fetch_chat()
                    detected_model = get_model_slug(chat)
        except Exception:
            detected_model = None

        if detected_model:
            if default_model and detected_model != default_model:
                choice = input(
                    f"Detected model slug '{detected_model}' from your latest chat. "
                    f"You configured '{default_model}'. Use detected slug instead? [y/N]: "
                ).strip().lower()
                if choice in {"y", "yes"}:
                    default_model = detected_model
            elif not default_model:
                default_model = detected_model
                print(f"Using detected model slug '{default_model}' for new chats.")

        if not default_model:
            prompt = (
                "No default model slug detected. Enter one now, or press Enter to skip: "
            )
            entered_model = input(prompt).strip()
            if entered_model:
                default_model = entered_model
            else:
                print(
                    "No model slug set. New chats may fail. "
                    "Provide --model, RE_GPT_MODEL, or config.ini session.model."
                )

        chatgpt.default_model = default_model
        print("\nSession established. Type 'exit', 'quit', or 'q' to leave the chat.")
        print("Use 'download <conversation_id|title>', 'download all', or 'download list' to export chats.")
        conversation = select_conversation(chatgpt, storage)

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

            def send_prompt_and_record() -> Optional[str]:
                response_text = stream_response(conversation.chat(prompt))
                conversation_id = conversation.conversation_id
                if conversation_id:
                    storage.append_message(
                        conversation_id,
                        author="user",
                        content=stripped_prompt,
                        create_time=time.time(),
                    )
                    if response_text:
                        storage.append_message(
                            conversation_id,
                            author="assistant",
                            content=response_text.strip(),
                            create_time=time.time(),
                        )
                return response_text

            try:
                send_prompt_and_record()
            except UnexpectedResponseError as exc:
                if is_token_expired_error(exc):
                    print(
                        "Authentication token expired. Refreshing session token...",
                        flush=True,
                    )
                    try:
                        chatgpt.refresh_auth_token()
                    except InvalidSessionToken:
                        print(
                            "The session token used for authentication is no longer valid. "
                            "Please restart the CLI with a fresh __Secure-next-auth.session-token."
                        )
                        break
                    except Exception as refresh_exc:  # noqa: BLE001
                        print(
                            "Failed to refresh the authentication token automatically: "
                            f"{refresh_exc}"
                        )
                        print(f"Encountered an error while chatting: {exc}")
                        continue
                    else:
                        print("Session refreshed. Retrying your message now...", flush=True)
                        try:
                            send_prompt_and_record()
                        except UnexpectedResponseError as retry_exc:
                            print(f"Encountered an error while chatting: {retry_exc}")
                        except Exception as retry_exc:  # noqa: BLE001
                            print(f"Encountered an error while chatting: {retry_exc}")
                        else:
                            continue
                        continue

                print(f"Encountered an error while chatting: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"Encountered an error while chatting: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(1)
