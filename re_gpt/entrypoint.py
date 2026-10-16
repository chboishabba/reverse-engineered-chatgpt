"""Top-level ``re-gpt`` router with browser-owned mutation support."""

from __future__ import annotations

import argparse
import functools
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from .read_plane import fetch_validated_conversation, install_validated_sync_read_plane
from .mutation_receipt import classify_prompt_mutation

# The supported executable always installs the validated sync read compatibility
# layer before delegating to the legacy CLI surface.
install_validated_sync_read_plane()

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
    """Resolve mutation prompt from flag, file, or piped stdin."""

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
    """Re-pull a mutated thread and optionally certify its new user node."""

    token = key or get_session_token()
    default_model = model or get_default_model()
    user_agent = get_default_user_agent()

    with ConversationStorage(write_json=export_json) as storage, SyncChatGPT(
        session_token=token,
        default_model=default_model,
        user_agent=user_agent,
    ) as chatgpt:
        chat = fetch_validated_conversation(chatgpt, conversation_id)
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

        mutation_receipt = None
        if expected_prompt is not None:
            mutation_receipt = classify_prompt_mutation(
                chat,
                expected_prompt,
                not_before=float(not_before or 0.0),
            )

        return {
            "conversation_id": conversation_id,
            "new_messages": result.new_messages,
            "total_messages": result.total_messages,
            "json_path": str(result.json_path) if result.json_path else None,
            "asset_count": len(result.asset_paths),
            "asset_errors": len(result.asset_errors),
            "mutation_prompt_present": (
                mutation_receipt.prompt_committed if mutation_receipt is not None else None
            ),
            "mutation_prompt_nodes": (
                list(mutation_receipt.user_node_ids) if mutation_receipt is not None else []
            ),
            "mutation_assistant": (
                mutation_receipt.assistant_text if mutation_receipt is not None else None
            ),
        }


def build_write_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="re-gpt",
        description=(
            "Submit to an existing ChatGPT conversation through a browser-owned "
            "accessibility transport and reconcile the result into the local archive."
        ),
    )
    parser.add_argument(
        "--send",
        required=True,
        metavar="CONVERSATION",
        help="Conversation UUID, URL, or relay alias.",
    )
    parser.add_argument("--prompt", help="Prompt text to submit.")
    parser.add_argument("--prompt-file", type=Path, help="Read prompt text from this file.")
    parser.add_argument(
        "--browser-profile",
        type=Path,
        help="Persistent browser profile used when the relay launches Chrome.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=("displayless", "headless", "headed"),
        default="displayless",
        help=(
            "displayless = normal Chrome on Ozone off-screen backend; "
            "headless = unified Chromium headless; headed = normal Chrome window "
            "(default: %(default)s). Ignored when --cdp-endpoint is used."
        ),
    )
    parser.add_argument(
        "--cdp-endpoint",
        help=(
            "Attach to an already-running Chromium/Chrome instance over CDP, for "
            "example http://127.0.0.1:9222. The relay will not close that browser."
        ),
    )
    parser.add_argument(
        "--write-timeout",
        type=int,
        default=900,
        help="Browser mutation timeout in seconds (default: 900).",
    )
    parser.add_argument(
        "--key",
        "-k",
        help="Session token used only for post-write archive reconciliation.",
    )
    parser.add_argument(
        "--model",
        "-m",
        help="Default model slug used by the reconciliation client.",
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Also write the reconciled conversation JSON export.",
    )
    return parser


def write_main(argv: list[str] | None = None) -> int:
    parser = build_write_parser()
    args = parser.parse_args(argv)
    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file, stdin=sys.stdin)
        mutation_started_at = time.time()
        mutation = submit_prompt(
            args.send,
            prompt,
            profile_dir=args.browser_profile,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
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
            expected_prompt=prompt,
            # Allow a small clock-skew / page-dispatch margin without letting an
            # old identical probe certify this operation.
            not_before=mutation_started_at - 5.0,
        )
    except Exception as exc:  # noqa: BLE001 - mutation may already be committed remotely.
        print(
            f"error: mutation succeeded via {mutation.get('transport', 'browser')} but "
            f"archive reconciliation failed for {conversation_id}: {exc}. "
            "Do not resend the prompt; re-run the pull/reconciliation only.",
            file=sys.stderr,
        )
        return 2

    mutation_state = str(mutation.get("mutation_state") or "")
    prompt_present = reconciled.get("mutation_prompt_present")
    reconciled_assistant = str(reconciled.get("mutation_assistant") or "").strip()

    if mutation_state == "submitted_unconfirmed":
        if prompt_present is False:
            print(
                "error: browser submission was observed locally but the new user prompt "
                "was not found in the canonical graph after reconciliation.",
                file=sys.stderr,
            )
            return 1
        if prompt_present is True and not reconciled_assistant:
            print(
                "error: canonical graph confirms the user prompt committed, but no "
                "assistant child is present yet. Do not resend; pull/reconcile later.",
                file=sys.stderr,
            )
            return 2

    # Even when the browser observed an assistant, a missing canonical user node
    # is an exactly-once ambiguity: archive state is authoritative, so do not
    # silently report success or resend.
    if prompt_present is False:
        print(
            "error: browser reported mutation progress but reconciliation did not "
            "find the new user node. Do not resend automatically; inspect/pull again.",
            file=sys.stderr,
        )
        return 2

    if reconciled_assistant and not latest:
        print(reconciled_assistant)

    print(
        "[re-gpt] reconciled {conversation_id}: +{new_messages} new message(s), "
        "{total_messages} total".format(**reconciled)
    )
    return 0


def main() -> int:
    """Route browser mutations here and delegate every legacy command unchanged."""

    if any(arg == "--send" or arg.startswith("--send=") for arg in sys.argv[1:]):
        return write_main(sys.argv[1:])
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
