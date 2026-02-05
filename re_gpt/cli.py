"Interactive command line interface for ChatGPT sessions."

from __future__ import annotations

import argparse
import functools
import subprocess
import shutil
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from .view_helpers import normalize_conversation_selector, parse_view_argument

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
        "Open the developer tools (F12 or Cmd+Opt=I on macOS).",
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
        print("Instantiating SyncChatGPT for verification...", flush=True)
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


def obtain_session_token(key: Optional[str] = None, allow_invalid_for_browser_login: bool = False) -> str:
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
        print("Finished checking for cached token. Proceeding to attempt verification.", flush=True)
        print("Attempting cached session token ...", flush=True)
        try:
            verify_session_token(cached_token)
            return cached_token
        except InvalidSessionToken:
            if allow_invalid_for_browser_login:
                print("Warning: Cached token rejected, but proceeding for browser login.")
                return cached_token # Return the token even if invalid to allow browser to launch
            print(
                "The cached token was rejected. Please grab a fresh "
                "`__Secure-next-auth.session-token`."
            )
        except TokenNotProvided:
            if allow_invalid_for_browser_login:
                print("Warning: Cached token empty, but proceeding for browser login.")
                return "" # Return empty string
            print("The cached token was empty. You'll need to paste a new one.")

    if allow_invalid_for_browser_login:
        print("Bypassing token input for browser login. Providing a placeholder token.")
        return "BROWSER_LOGIN_PLACEHOLDER_TOKEN"

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


def _line_range_indices(
    lines_range: Optional[Tuple[int, Optional[int]]]
) -> Tuple[Optional[int], Optional[int]]:
    """Convert a 1-based ``lines`` range to zero-based start/end indices."""

    if not lines_range:
        return None, None

    start = lines_range[0] - 1
    end = None if lines_range[1] is None else lines_range[1] - 1
    return start, end


def _filter_messages(
    messages: List[Dict],
    start_idx: Optional[int],
    end_idx: Optional[int],
    since_index: Optional[int],
) -> List[Dict]:
    """Return the subset of *messages* matching the slicing parameters."""

    result: List[Dict] = []
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

        result.append(message)
    return result


def _build_conversation_lines(
    conversation_title: Optional[str],
    conversation_id: str,
    messages: List[Dict],
) -> List[str]:
    """Turn *messages* into the string lines that a viewer should see."""

    header_title = conversation_title or "(no title)"
    lines = [f"--- Conversation: {header_title} ({conversation_id}) ---"]
    for message in messages:
        author = message.get("author", "unknown")
        content = message.get("content", "")
        try:
            index = int(message.get("message_index"))
        except (TypeError, ValueError):
            index = 0
        lines.append(f"{author.capitalize()} [{index + 1}]: {content}")
    lines.append("--- End of conversation ---")
    return lines


def _build_notice_message(
    filtered_messages: List[Dict],
    since_last_update: bool,
    lines_range: Optional[Tuple[int, Optional[int]]],
) -> str:
    """Return the message shown when no entries match the configured filters."""

    if filtered_messages:
        return ""
    if since_last_update:
        return "No new messages since the last update."
    if lines_range:
        return "No messages match the requested range."
    return ""


def _should_download_since_last(
    conversation_id: str,
    storage: ConversationStorage,
) -> bool:
    """Return True if the stored metadata suggests new messages are available."""

    summary = storage.get_conversation_summary(conversation_id)
    if not summary:
        return True

    remote_update = summary.get("remote_update_time")
    last_seen = summary.get("last_seen_at")
    if remote_update is None or last_seen is None:
        return True

    return remote_update > last_seen


def _collect_conversation_catalog(chatgpt: SyncChatGPT, storage: ConversationStorage) -> List[Dict]:
    """Fetch conversation headers in pages and persist the catalog locally."""
    print("Fetching conversation catalog in pages...", flush=True)
    all_conversations = []
    offset = 0
    # Fetch a reasonable number of pages to build a catalog for matching.
    num_pages_to_fetch = 5 
    
    for page_num in range(num_pages_to_fetch):
        try:
            page_data = chatgpt.list_conversations_page(offset, CONVERSATION_PAGE_SIZE)
            items = page_data.get("items", [])
            if not items:
                print("No more conversation pages to fetch.")
                break 
            all_conversations.extend(items)
            offset += len(items)
            print(f"Fetched catalog page {page_num + 1}/{num_pages_to_fetch}...", flush=True)
        except Exception as e:
            print(f"Error fetching catalog page {page_num + 1}: {e}. Stopping.", flush=True)
            break
            
    storage.record_conversations(all_conversations)
    return all_conversations


def _match_conversation_selector(argument: str, catalog: List[Dict]) -> Optional[Dict]:
    """Find the catalog entry whose ID or title matches *argument*."""

    selector = normalize_conversation_selector(argument.strip())
    if not selector:
        return None

    guess = selector.lower()
    for entry in catalog:
        entry_id = entry.get("id")
        if entry_id and str(entry_id).lower() == guess:
            return entry

    for entry in catalog:
        title = (entry.get("title") or "").lower()
        if title == guess:
            return entry

    return None


def _format_timestamp(value: Optional[float]) -> str:
    """Render *value* as ISO8601 with an epoch fallback."""

    if value is None:
        return "n/a"

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value)

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return f"{dt.isoformat()} (epoch {timestamp:.3f})"


def run_latest_command(storage: ConversationStorage) -> None:
    """Print the most recent assistant message stored locally."""

    if isinstance(storage, NullConversationStorage):
        print("Storage disabled; '--latest' requires conversation persistence.")
        return

    latest = storage.get_latest_message(author="assistant")
    if not latest:
        print("No cached assistant messages found.")
        return

    title = latest.get("title") or "(no title)"
    conversation_id = latest.get("conversation_id") or "unknown"
    timestamp = _format_timestamp(latest.get("create_time"))
    content = latest.get("content") or ""

    print(f"{title} ({conversation_id})")
    print(f"{timestamp} assistant: {content}")


def run_noninteractive_view(
    argument: str,
    chatgpt: SyncChatGPT,
    storage: ConversationStorage,
    since_last_override: bool = False,
) -> None:
    """Handle the `--view` automation mode without entering the interactive loop."""

    target, lines_range, since_last_update = parse_view_argument(argument)
    since_last_update = since_last_update or since_last_override
    if not target:
        print(
            "Usage: --view \"<conversation_id|title> [lines START[-END]] [since last update]\""
        )
        return

    conversation = None
    conversation_id = None
    conversation_title = None

    # Try to fetch by ID first (even if it doesn't look like a UUID).
    try:
        conversation_id = target
        conversation = chatgpt.get_conversation(conversation_id)
        chat = conversation.fetch_chat()
        conversation_title = conversation.title
    except Exception:
        conversation = None  # Failed, will try to match by title

    if conversation is None:
        print("Could not fetch by ID, trying to match by title...", flush=True)
        catalog = _collect_conversation_catalog(chatgpt, storage)
        matching_entry = _match_conversation_selector(target, catalog)
        if not matching_entry:
            print(f"Failed to find conversation matching '{target}'.")
            return

        conversation_id = matching_entry.get("id")
        conversation_title = matching_entry.get("title")
        try:
            conversation = chatgpt.get_conversation(
                conversation_id, title=conversation_title
            )
            chat = conversation.fetch_chat()
        except Exception as exc:
            print(f"Failed to fetch conversation {conversation_id}: {exc}")
            return

    messages = extract_ordered_messages(chat)
    since_index: Optional[int] = None
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

    start_idx, end_idx = _line_range_indices(lines_range)
    filtered_messages = _filter_messages(messages, start_idx, end_idx, since_index)
    notice_message = _build_notice_message(filtered_messages, since_last_update, lines_range)
    lines = _build_conversation_lines(
        conversation.title or conversation_title,
        conversation_id,
        filtered_messages,
    )

    if notice_message:
        print(notice_message)
    for line in lines:
        print(line)


def run_inspect_command(
    argument: str,
    chatgpt: SyncChatGPT,
    storage: ConversationStorage,
) -> None:
    """Handle the `--inspect` automation mode without entering the interactive loop."""

    selector = normalize_conversation_selector(argument.strip())
    if not selector:
        print("Usage: --inspect <conversation_id|title>")
        return

    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    remote_update_time: Optional[float] = None
    summary: Optional[Dict] = None

    catalog: Optional[List[Dict]] = None
    try:
        catalog = _collect_conversation_catalog(chatgpt, storage)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to fetch conversation catalog: {exc}")

    if catalog:
        matched_entry = _match_conversation_selector(selector, catalog)
        if matched_entry:
            conversation_id = matched_entry.get("id")
            conversation_title = matched_entry.get("title")
            remote_update_time = matched_entry.get("update_time")

    if conversation_id:
        summary = storage.get_conversation_summary(conversation_id)
    else:
        summary = storage.get_conversation_summary(selector)
        if summary:
            conversation_id = selector
            conversation_title = conversation_title or summary.get("title")

    if not conversation_id:
        matches = storage.search_conversations(selector)
        if len(matches) == 1:
            conversation_id = matches[0].get("id")
            conversation_title = matches[0].get("title")
            summary = storage.get_conversation_summary(conversation_id)
        elif len(matches) > 1:
            print(f"Found {len(matches)} cached conversations matching '{selector}':")
            for match in matches:
                title = match.get("title") or "(no title)"
                cid = match.get("id") or ""
                print(f"- {title} [{cid}]")
            return

    if not conversation_id:
        print(f"Failed to find conversation matching '{selector}'.")
        return

    print(f"Conversation ID: {conversation_id}")
    if conversation_title or (summary and summary.get("title")):
        print(f"Title: {conversation_title or summary.get('title')}")

    if summary:
        print(f"Discovered at: {_format_timestamp(summary.get('discovered_at'))}")
        print(f"Last seen at: {_format_timestamp(summary.get('last_seen_at'))}")
        print(
            "Remote update time (cached): "
            f"{_format_timestamp(summary.get('remote_update_time'))}"
        )
        print(f"Cached message count: {summary.get('cached_message_count', 0)}")
    else:
        print("Cached metadata: n/a (storage disabled or no local record)")

    if remote_update_time is not None:
        print(f"Remote update time (catalog): {_format_timestamp(remote_update_time)}")
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

        start_idx, end_idx = _line_range_indices(lines_range)
        filtered_messages = _filter_messages(messages, start_idx, end_idx, since_index)
        notice_message = _build_notice_message(filtered_messages, since_last_update, lines_range)
        if notice_message:
            print(notice_message)

        lines = _build_conversation_lines(
            conversation.title or conversation_title,
            conversation_id,
            filtered_messages,
        )
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp_file:
            tmp_file.write("\n".join(lines))
            tmp_file.write("\n")
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

        normalized_command = normalize_conversation_selector(command)
        if normalized_command != command:
            command = normalized_command

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
    since_last_update: bool = False,
) -> None:
    """Download and persist conversations based on ``user_input``."""

    if since_last_update and isinstance(storage, NullConversationStorage):
        print("Storage disabled; '--since-last' requires conversation persistence.")
        return

    parts = user_input.strip().split(maxsplit=1)
    if len(parts) < 2:
        print("Usage: download <conversation_id|title|all|list>")
        return

    arg = normalize_conversation_selector(parts[1])
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
        if since_last_update:
            targets = [
                cid for cid in targets if cid and _should_download_since_last(cid, storage)
            ]
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

    if since_last_update and targets:
        filtered_targets = [
            cid for cid in targets if cid and _should_download_since_last(cid, storage)
        ]
        if not filtered_targets:
            print("No conversations have updates since the last download.")
            return
        targets = filtered_targets

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
        "--view",
        type=str,
        help=(
            "Print messages for a conversation without entering the interactive loop. "
            "Supports optional `lines START[-END]` and `since last update` selectors."
        ),
    )
    parser.add_argument(
        "--inspect",
        type=str,
        help="Show cached metadata for a conversation (id or title).",
    )
    parser.add_argument(
        "--download",
        type=str,
        help="Persist a conversation (`all`, `list`, or a conversation id) and exit.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Print the latest cached assistant message and exit.",
    )
    parser.add_argument(
        "--since-last",
        action="store_true",
        help="Limit --view/--download to messages since the last cached update.",
    )
    parser.add_argument(
        "--nostore",
        action="store_true",
        help="Do not persist conversations or append messages to SQLite.",
    )
    parser.add_argument(
        "--browser-login",
        action="store_true",
        help="Launch a browser to log in to ChatGPT.",
    )
    args = parser.parse_args()
    automation_flags = sum(
        1
        for flag in (
            args.list,
            args.view,
            args.inspect,
            args.download,
            args.latest,
            args.browser_login,
        )
        if bool(flag)
    )
    if automation_flags > 1:
        parser.error(
            "Choose only one of --list, --view, --inspect, --download, --latest "
            "or --browser-login at a time."
        )
    if args.since_last and not (args.view or args.download):
        parser.error("--since-last requires --view or --download.")
    
    storage_context = NullConversationStorage() if args.nostore else ConversationStorage()
    if args.latest:
        with storage_context as storage:
            run_latest_command(storage)
        return

    if args.browser_login:
        token = obtain_session_token(args.key, allow_invalid_for_browser_login=True)
    else:
        token = obtain_session_token(args.key)

    default_model = args.model or get_default_model()
    user_agent = get_default_user_agent()

    with storage_context as storage, SyncChatGPT(
        session_token=token, default_model=default_model, user_agent=user_agent
    ) as chatgpt:
        if args.nostore:
            print("Storage disabled; conversations will not be saved locally.")
        
        if args.list:
            all_conversations: list[dict] = []
            try:
                all_conversations = chatgpt.list_all_conversations()
            except Exception as exc:  # noqa: BLE001 - fallback to paging.
                print(f"List-all failed ({exc}); falling back to paging.", flush=True)
                print("Fetching conversations in pages to debug potential hang...", flush=True)
                offset = 0
                page_limit = CONVERSATION_PAGE_SIZE # This is 10
                num_pages_to_fetch = 3

                for _ in range(num_pages_to_fetch):
                    try:
                        page_data = chatgpt.list_conversations_page(offset, page_limit)
                        items = page_data.get("items", [])
                        if not items:
                            break # No more conversations
                        all_conversations.extend(items)
                        offset += page_limit
                        print(
                            f"Fetched page {(_ + 1)}/{num_pages_to_fetch} "
                            f"({len(items)} conversations)...",
                            flush=True,
                        )
                    except Exception as page_exc:
                        print(
                            f"Error fetching page {(_ + 1)}: {page_exc}. Stopping fetch.",
                            flush=True,
                        )
                        break

            if not all_conversations:
                print("No conversations found or fetched.")
            else:
                print(f"Fetched a total of {len(all_conversations)} conversations.")
                storage.record_conversations(all_conversations) # Still record what was fetched
                for conv in all_conversations:
                    cid = conv.get("id") or ""
                    title = conv.get("title") or "(no title)"
                    print(f"{cid}\t{title}")
        elif args.view:
            run_noninteractive_view(args.view, chatgpt, storage, since_last_override=args.since_last)
        elif args.inspect:
            run_inspect_command(args.inspect, chatgpt, storage)
        elif args.download:
            handle_download_command(
                f"download {args.download}",
                chatgpt,
                storage,
                since_last_update=args.since_last,
            )
        elif args.browser_login:
            chatgpt.start_browser_session()
            sys.exit(0)
        else:
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
                        except Exception as refresh_exc: # noqa: BLE001
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
                            except Exception as retry_exc: # noqa: BLE001
                                print(f"Encountered an error while chatting: {retry_exc}")
                            else:
                                continue
                            continue

                    print(f"Encountered an error while chatting: {exc}")
                except Exception as exc: # noqa: BLE001
                    print(f"Encountered an error while chatting: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(1)
