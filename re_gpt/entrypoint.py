"""Top-level ``re-gpt`` router with browser-owned canonical mutation service."""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path
from typing import Any, TextIO

from .read_plane import fetch_validated_conversation, install_validated_sync_read_plane
from .mutation_receipt import classify_prompt_mutation
install_validated_sync_read_plane()

from .browser_write import BrowserWriteError
from .cli import main as legacy_main
from .mutation_service import MutationService
from .storage import ConversationStorage, extract_ordered_messages
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token


def resolve_prompt(prompt: str | None, prompt_file: Path | None, *, stdin: TextIO) -> str:
    if prompt is not None and prompt_file is not None:
        raise ValueError("provide either --prompt or --prompt-file, not both")
    if prompt is not None:
        resolved = prompt
    elif prompt_file is not None:
        resolved = prompt_file.read_text(encoding="utf-8")
    elif not getattr(stdin, "isatty", lambda: True)():
        resolved = stdin.read()
    else:
        raise ValueError("provide --prompt, --prompt-file, or pipe prompt text on stdin")
    resolved = resolved.strip()
    if not resolved:
        raise ValueError("prompt is empty")
    return resolved


def reconcile_conversation(
    conversation_id: str,
    *,
    key: str | None = None,
    model: str | None = None,
    export_json: bool = False,
    expected_prompt: str | None = None,
    not_before: float | None = None,
) -> dict[str, Any]:
    """Compatibility helper for explicit archive reconciliation."""
    token = key or get_session_token()
    default_model = model or get_default_model()
    user_agent = get_default_user_agent()
    with ConversationStorage(write_json=export_json) as storage, SyncChatGPT(
        session_token=token, default_model=default_model, user_agent=user_agent,
    ) as chatgpt:
        chat = fetch_validated_conversation(chatgpt, conversation_id)
        messages = extract_ordered_messages(chat)
        asset_fetcher = functools.partial(chatgpt.download_asset, conversation_id=conversation_id) if hasattr(chatgpt, "download_asset") else None
        result = storage.persist_chat(conversation_id, chat, messages, asset_fetcher=asset_fetcher)
        receipt = classify_prompt_mutation(chat, expected_prompt, not_before=float(not_before or 0.0)) if expected_prompt is not None else None
        return {
            "conversation_id": conversation_id,
            "new_messages": result.new_messages,
            "total_messages": result.total_messages,
            "json_path": str(result.json_path) if result.json_path else None,
            "asset_count": len(result.asset_paths),
            "asset_errors": len(result.asset_errors),
            "mutation_prompt_present": receipt.prompt_committed if receipt else None,
            "mutation_prompt_nodes": list(receipt.user_node_ids) if receipt else [],
            "mutation_assistant": receipt.assistant_text if receipt else None,
        }


def build_write_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="re-gpt", description="Continue an existing ChatGPT thread through the authenticated DOM and certify it against the canonical graph.")
    parser.add_argument("--send", required=True, metavar="CONVERSATION", help="Conversation UUID, URL, or relay alias.")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--attach", action="append", type=Path, default=[], help="Attach a file using ChatGPT's native file input; repeat for multiple files.")
    parser.add_argument("--browser-profile", type=Path, help="Persistent authenticated browser profile.")
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint", help="Attach to an already-running browser over CDP.")
    parser.add_argument("--write-transport", choices=("dom", "async"), default="dom", help="Mutation transport: dom (default production) or async (opt-in HTTP probe).")
    parser.add_argument("--write-timeout", type=int, default=60, help="Browser readiness/attachment/submission budget in seconds.")
    parser.add_argument("--key", "-k", help="Session token used only for canonical reconciliation.")
    parser.add_argument("--model", "-m")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--follow", action="store_true", help="After commit, keep reconciling until an assistant child appears.")
    parser.add_argument("--follow-timeout", type=float, default=60.0)
    return parser


def write_main(argv: list[str] | None = None) -> int:
    args = build_write_parser().parse_args(argv)
    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file, stdin=sys.stdin)
        service = MutationService(session_token=args.key, model=args.model, export_json=args.export_json)
        result = service.continue_thread(
            args.send,
            prompt,
            attachments=args.attach,
            profile_dir=args.browser_profile,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            browser_timeout=args.write_timeout,
            write_transport=args.write_transport,
            follow=args.follow,
            follow_timeout=args.follow_timeout,
        )
    except (BrowserWriteError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: mutation status is uncertain: {exc}. Do not resend automatically.", file=sys.stderr)
        return 2

    state = str(result.get("state") or "commit_unknown")
    operation_id = str(result.get("operation_id") or "")
    assistant = str(result.get("assistant_text") or "").strip()
    if assistant:
        print(assistant)
    attachment_ids = result.get("attachment_ids") or []
    suffix = f" attachments={','.join(attachment_ids)}" if attachment_ids else ""

    if state == "complete":
        print(f"[re-gpt] complete operation={operation_id} conversation={result.get('conversation_id')} user_node={result.get('user_node_id')} assistant_node={result.get('assistant_node_id')}{suffix}")
        return 0
    if state in {"committed", "committed_pending"}:
        print(f"[re-gpt] {state} operation={operation_id} conversation={result.get('conversation_id')} user_node={result.get('user_node_id')}{suffix}")
        return 0

    print(f"error: operation={operation_id} state={state}; canonical commit is not proven. Do not resend automatically; use operation status/reconciliation.", file=sys.stderr)
    return 2


def main() -> int:
    if any(arg == "--send" or arg.startswith("--send=") for arg in sys.argv[1:]):
        return write_main(sys.argv[1:])
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
