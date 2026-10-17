"""Top-level ``re-gpt`` router over the canonical MutationService."""

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
    parser = argparse.ArgumentParser(
        prog="re-gpt",
        description="API-first ChatGPT graph mutations with canonical graph receipts and safe DOM fallback for tail continuation.",
    )
    mutation = parser.add_mutually_exclusive_group(required=True)
    mutation.add_argument("--send", metavar="CONVERSATION", help="Continue an existing conversation.")
    mutation.add_argument("--new", action="store_true", help="Create a new conversation directly and return its assigned ID.")
    mutation.add_argument("--branch", metavar="CONVERSATION", help="Create a user branch from --parent-node.")
    mutation.add_argument("--regenerate", metavar="CONVERSATION", help="Regenerate from an exact --user-node.")
    mutation.add_argument("--edit-branch", metavar="CONVERSATION", help="Replace an exact --user-node and create a branch.")
    parser.add_argument("--parent-node", help="Exact graph node used as the parent for --branch.")
    parser.add_argument("--user-node", help="Exact user node for --regenerate or --edit-branch.")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--attach", action="append", type=Path, default=[], help="Attach a file on continue/branch/edit; repeat for multiple files.")
    parser.add_argument("--browser-profile", type=Path, help="Persistent authenticated browser profile used only for bootstrap/fallback.")
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint", help="Attach to an already-running browser for bootstrap/fallback.")
    parser.add_argument("--write-transport", choices=("dom", "async", "auto"), default="async", help="Existing-thread continuation transport; default: async.")
    parser.set_defaults(dom_fallback=True)
    parser.add_argument("--dom-fallback", dest="dom_fallback", action="store_true", help="Allow DOM only after an explicit pre-commit async rejection (default).")
    parser.add_argument("--no-dom-fallback", dest="dom_fallback", action="store_false", help="Disable pre-commit DOM fallback.")
    parser.add_argument("--write-timeout", type=int, default=60)
    parser.add_argument("--key", "-k", help="Session token.")
    parser.add_argument("--model", "-m")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--follow", action="store_true", help="Keep reconciling until a visible assistant descendant appears.")
    parser.add_argument("--follow-timeout", type=float, default=60.0)
    return parser


def _print_result(result: dict[str, Any]) -> int:
    state = str(result.get("state") or "commit_unknown")
    operation_id = str(result.get("operation_id") or "")
    assistant = str(result.get("assistant_text") or "").strip()
    if assistant:
        print(assistant)
    attachment_ids = result.get("attachment_ids") or []
    suffix = f" attachments={','.join(attachment_ids)}" if attachment_ids else ""
    conversation_id = result.get("conversation_id")

    if state == "complete":
        print(
            f"[re-gpt] complete operation={operation_id} conversation={conversation_id} "
            f"user_node={result.get('user_node_id')} assistant_node={result.get('assistant_node_id')}{suffix}"
        )
        return 0
    if state in {"committed", "committed_pending"}:
        print(
            f"[re-gpt] {state} operation={operation_id} conversation={conversation_id} "
            f"user_node={result.get('user_node_id')}{suffix}"
        )
        return 0

    print(
        f"error: operation={operation_id} state={state}; canonical commit is not proven. "
        "Do not resend automatically; use operation status/reconciliation.",
        file=sys.stderr,
    )
    return 2


def write_main(argv: list[str] | None = None) -> int:
    args = build_write_parser().parse_args(argv)
    needs_prompt = bool(args.send or args.new or args.branch or args.edit_branch)
    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file, stdin=sys.stdin) if needs_prompt else None
        service = MutationService(session_token=args.key, model=args.model, export_json=args.export_json)
        common = {
            "profile_dir": args.browser_profile,
            "browser_mode": args.browser_mode,
            "cdp_endpoint": args.cdp_endpoint,
            "browser_timeout": args.write_timeout,
            "follow": args.follow,
            "follow_timeout": args.follow_timeout,
        }
        if args.send:
            result = service.continue_thread(
                args.send,
                prompt or "",
                attachments=args.attach,
                write_transport=args.write_transport,
                dom_fallback=args.dom_fallback,
                **common,
            )
        elif args.new:
            if args.attach:
                raise ValueError("--new does not stage attachments yet; create the thread then continue it with --attach")
            result = service.new_conversation(prompt or "", **common)
        elif args.branch:
            if not args.parent_node:
                raise ValueError("--branch requires --parent-node")
            result = service.branch_from_node(
                args.branch,
                args.parent_node,
                prompt or "",
                attachments=args.attach,
                **common,
            )
        elif args.regenerate:
            if not args.user_node:
                raise ValueError("--regenerate requires --user-node")
            if args.attach:
                raise ValueError("--regenerate does not accept attachments")
            result = service.regenerate(args.regenerate, args.user_node, **common)
        else:
            if not args.user_node:
                raise ValueError("--edit-branch requires --user-node")
            result = service.edit_and_branch(
                args.edit_branch,
                args.user_node,
                prompt or "",
                attachments=args.attach,
                **common,
            )
    except (BrowserWriteError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - do not convert uncertain mutations into retries.
        print(f"error: mutation status is uncertain: {exc}. Do not resend automatically.", file=sys.stderr)
        return 2
    return _print_result(result)


def main() -> int:
    graph_flags = ("--send", "--new", "--branch", "--regenerate", "--edit-branch")
    if any(arg == flag or arg.startswith(flag + "=") for arg in sys.argv[1:] for flag in graph_flags):
        return write_main(sys.argv[1:])
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
