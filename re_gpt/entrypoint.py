"""Top-level ``re-gpt`` command router.

Read-only and interactive behavior stays in :mod:`re_gpt.cli`.  Existing-thread
mutations use the browser-owned write transport and are reconciled immediately
through the normal remote read/archive path.
"""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path
from typing import Any, TextIO

from .browser_write import BrowserWriteError, submit_prompt
from .cli import main as legacy_main
from .storage import ConversationStorage, extract_ordered_messages
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token


def resolve_prompt(
    prompt: str | None,
    prompt_file: Path | None,
    *,
    stdin: TextIO,
) -> str:
    """Resolve mutation prompt text from flag, file, or piped stdin."""

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
) -> dict[str, Any]:
    """Re-pull a mutated conversation into the canonical local archive."""

    token = key or get_session_token()
    default_model = model or get_default_model()
    user_agent = get_default_user_agent()

    with ConversationStorage(write_json=export_json) as storage, SyncChatGPT(
        session_token=token,
        default_model=default_model,
        user_agent=user_agent,
    ) as chatgpt:
        conversation = chatgpt.get_conversation(conversation_id)
        chat = conversation.fetch_chat()
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
        return {
            "conversation_id": conversation_id,
            "new_messages": result.new_messages,
            "total_messages": result.total_messages,
            "json_path": str(result.json_path) if result.json_path else None,
            "asset_count": len(result.asset_paths),
            "asset_errors": len(result.asset_errors),
        }


def build_write_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="re-gpt",
        description=(
            "Submit to an existing ChatGPT conversation through an authenticated "
            "browser and reconcile the result into the local archive."
        ),
    )
    parser.add_argument("--send", required=True, metavar="CONVERSATION", help="Conversation UUID, URL, or relay alias.")
    parser.add_argument("--prompt", help="Prompt text to submit.")
    parser.add_argument("--prompt-file", type=Path, help="Read prompt text from this file.")
    parser.add_argument("--browser-profile", type=Path, help="Persistent Chrome profile used by the relay.")
    parser.add_argument("--headed", action="store_true", help="Use headed Chrome (more reliable through Cloudflare).")
    parser.add_argument("--write-timeout", type=int, default=900, help="Browser mutation timeout in seconds (default: 900).")
    parser.add_argument("--key", "-k", help="Session token used only for post-write archive reconciliation.")
    parser.add_argument("--model", "-m", help="Default model slug used by the reconciliation client.")
    parser.add_argument("--export-json", action="store_true", help="Also emit the reconciled conversation JSON export.")
    return parser


def write_main(argv: list[str] | None = None) -> int:
    parser = build_write_parser()
    args = parser.parse_args(argv)
    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file, stdin=sys.stdin)
        mutation = submit_prompt(
            args.send,
            prompt,
            profile_dir=args.browser_profile,
            headed=args.headed,
            timeout=args.write_timeout,
        )
    except (BrowserWriteError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    latest = str(mutation.get("latest_assistant") or "").strip()
    if latest:
        print(latest)

    conversation_id = str(
        mutation.get("resolved_thread_id")
        or mutation.get("conversation_id")
        or args.send
    ).strip()
    try:
        reconciled = reconcile_conversation(
            conversation_id,
            key=args.key,
            model=args.model,
            export_json=args.export_json,
        )
    except Exception as exc:  # noqa: BLE001 - mutation succeeded; report reconciliation separately.
        print(
            f"error: mutation succeeded via {mutation.get('transport', 'browser')} but "
            f"archive reconciliation failed for {conversation_id}: {exc}",
            file=sys.stderr,
        )
        return 2

    print(
        "[re-gpt] reconciled {conversation_id}: +{new_messages} new message(s), "
        "{total_messages} total".format(**reconciled),
        file=sys.stdout,
    )
    return 0


def main() -> int:
    """Route write mutations to the browser transport; delegate everything else."""

    if "--send" in sys.argv[1:]:
        return write_main(sys.argv[1:])
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
