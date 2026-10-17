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
from .revive import revive_thread
from .fleet import FleetOrchestrator
from .gap_comparator import ExactGapComparator
from .storage import ConversationStorage, DEFAULT_DB_PATH, extract_ordered_messages
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
    mutation.add_argument("--revive", metavar="CONVERSATION", help="Revive a long conversation into a fresh thread with context attached.")
    mutation.add_argument("--fleet", action="store_true", help="Display full fleet coordination matrix across all clusters.")
    mutation.add_argument("--fleet-revive-fatigued", action="store_true", help="Automatically revive all fatigued lead threads across all clusters.")
    mutation.add_argument("--fleet-alpha", action="store_true", help="Rank and display high-alpha mathematical & foundational continuations across recent threads.")
    mutation.add_argument("--pnf-fleet", action="store_true", help="Compile PNF semantic factor and world model state for recent threads via slr prod compiler.")
    mutation.add_argument("--compare-gap", metavar="CONVERSATION", help="Compare a conversation's PNF multiset against a formal specification file.")
    mutation.add_argument("--inspect-thread", metavar="CONVERSATION", help="Inspect a thread's title, turns, and recent messages.")
    mutation.add_argument("--branch", metavar="CONVERSATION", help="Create a user branch from --parent-node.")
    mutation.add_argument("--regenerate", metavar="CONVERSATION", help="Regenerate from an exact --user-node.")
    mutation.add_argument("--edit-branch", metavar="CONVERSATION", help="Replace an exact --user-node and create a branch.")
    parser.add_argument("--turns", type=int, default=3, help="Number of turns to show for --inspect-thread.")
    parser.add_argument("--alpha-limit", type=int, default=200, help="Number of recent threads to scan for --fleet-alpha.")
    parser.add_argument("--min-score", type=float, default=25.0, help="Minimum score threshold for --fleet-alpha.")
    parser.add_argument("--pnf-limit", type=int, default=200, help="Number of recent threads to compile PNF for (default: 200).")
    parser.add_argument("--pnf-output", type=Path, default=None, help="Output directory for .slro, .slrw and summary files.")
    parser.add_argument("--formal-file", type=Path, help="Path to formal Agda/Lean file for --compare-gap.")
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
    parser.add_argument("--output", "-o", type=Path, help="Write assistant response text directly to this file.")
    parser.add_argument("--stream", action="store_true", help="Stream token deltas live to stdout as they arrive.")
    return parser


def _print_result(
    result: dict[str, Any],
    *,
    output_file: Path | None = None,
    streamed: bool = False,
) -> int:
    state = str(result.get("state") or "commit_unknown")
    operation_id = str(result.get("operation_id") or "")
    assistant = str(result.get("assistant_text") or "").strip()
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(assistant + ("\n" if assistant else ""), encoding="utf-8")
    if assistant and not streamed:
        print(assistant)
    elif streamed:
        sys.stdout.write("\n")
        sys.stdout.flush()
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
    needs_prompt = bool(args.send or args.new or args.branch or args.edit_branch or args.revive)
    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file, stdin=sys.stdin) if needs_prompt else None
        service = MutationService(session_token=args.key, model=args.model, export_json=args.export_json)
        streamed = bool(args.stream)

        def on_progress(event: dict[str, Any]) -> None:
            if event.get("kind") == "token_delta":
                delta = str(event.get("delta") or "")
                if delta:
                    sys.stdout.write(delta)
                    sys.stdout.flush()

        common = {
            "profile_dir": args.browser_profile,
            "browser_mode": args.browser_mode,
            "cdp_endpoint": args.cdp_endpoint,
            "browser_timeout": args.write_timeout,
            "follow": args.follow or streamed,
            "follow_timeout": args.follow_timeout,
        }
        if args.fleet:
            orchestrator = FleetOrchestrator(
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                profile_dir=args.browser_profile,
                browser_mode=args.browser_mode,
                session_token=args.key,
                model=args.model,
            )
            print(orchestrator.render_matrix_markdown())
            return 0
        elif args.fleet_revive_fatigued:
            orchestrator = FleetOrchestrator(
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                profile_dir=args.browser_profile,
                browser_mode=args.browser_mode,
                session_token=args.key,
                model=args.model,
            )
            revivals = orchestrator.auto_revive_fatigued()
            print(f"Revived {len(revivals)} fatigued worker threads:")
            for r in revivals:
                print(f"- [{r['cluster']}] {r['title']}: {r['parent_conversation_id'][:8]} -> {str(r['revived_conversation_id'])[:8]} ({r['prior_turn_count']} prior turns)")
            return 0
        elif args.fleet_alpha:
            orchestrator = FleetOrchestrator(
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                profile_dir=args.browser_profile,
                browser_mode=args.browser_mode,
                session_token=args.key,
                model=args.model,
            )
            print(orchestrator.render_alpha_matrix(limit=args.alpha_limit, min_score=args.min_score))
            return 0
        elif args.pnf_fleet:
            orchestrator = FleetOrchestrator(
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                profile_dir=args.browser_profile,
                browser_mode=args.browser_mode,
                session_token=args.key,
                model=args.model,
            )
            print(f"Compiling PNF semantic factors for top {args.pnf_limit} threads via slr world compiler...")
            def on_pnf_prog(i, total, cid, title):
                if i % 20 == 0 or i == total:
                    print(f"  [{i}/{total}] Encoded observations for {cid[:8]} ({title[:30]})...")
            res = orchestrator.compile_fleet_pnf(limit=args.pnf_limit, output_dir=args.pnf_output, progress_cb=on_pnf_prog)
            receipt = res.get("compiler_receipt", {})
            print(f"\n✅ PNF Fleet Compilation Complete!")
            print(f"- Threads compiled: {res['threads_compiled']}")
            print(f"- Total sentences: {res['total_sentences']}")
            print(f"- Total tokens: {res['total_tokens']}")
            print(f"- Source manifestations: {receipt.get('manifestations')}")
            print(f"- PNF candidates: {receipt.get('pnf_candidates')}")
            print(f"- World atoms: {receipt.get('world_atoms')}")
            print(f"- Unique semantic factors (role, lemma, dependency): {res['unique_factors_count']}")
            print(f"- Durable world store: {res['slrw_path']} ({res['slrw_bytes']} bytes)")
            print(f"\nTop 10 Semantic Factors:")
            for f in res.get("top_factors", [])[:10]:
                print(f"  • {f['factor']}: {f['count']}")
            return 0
        elif args.inspect_thread:
            orchestrator = FleetOrchestrator(
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                profile_dir=args.browser_profile,
                browser_mode=args.browser_mode,
                session_token=args.key,
                model=args.model,
            )
            print(orchestrator.inspect_thread(args.inspect_thread, n_turns=args.turns))
            return 0
        elif args.compare_gap:
            if not args.formal_file:
                raise ValueError("--compare-gap requires --formal-file")
            comparator = ExactGapComparator()
            report = comparator.compare(args.compare_gap, args.formal_file)
            print(report.render_markdown())
            return 0
        elif args.send:
            result = service.continue_thread(
                args.send,
                prompt or "",
                attachments=args.attach,
                write_transport=args.write_transport,
                dom_fallback=args.dom_fallback,
                progress_callback=on_progress if streamed else None,
                **common,
            )
        elif args.revive:
            result = revive_thread(
                args.revive,
                prompt or "",
                attachments=args.attach,
                write_transport=args.write_transport,
                dom_fallback=args.dom_fallback,
                progress_callback=on_progress if streamed else None,
                db_path=getattr(service, "db_path", DEFAULT_DB_PATH),
                session_token=args.key,
                model=args.model,
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
    return _print_result(result, output_file=args.output, streamed=streamed)


def main() -> int:
    graph_flags = (
        "--send", "--new", "--revive", "--fleet", "--fleet-revive-fatigued",
        "--fleet-alpha", "--pnf-fleet", "--compare-gap", "--inspect-thread", "--branch", "--regenerate", "--edit-branch",
    )
    legacy_flags = (
        "--list", "--view", "--inspect", "--download", "--latest", "--since-last",
        "--nostore", "--browser-login", "--list-follow-out",
    )
    if any(arg == flag or arg.startswith(flag + "=") for arg in sys.argv[1:] for flag in graph_flags):
        return write_main(sys.argv[1:])
    if any(arg == flag or arg.startswith(flag + "=") for arg in sys.argv[1:] for flag in legacy_flags):
        legacy_main()
        return 0
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        return write_main(["--help"])
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
