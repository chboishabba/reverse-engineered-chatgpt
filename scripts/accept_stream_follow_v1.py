#!/usr/bin/env python3
"""Live acceptance for stream-first follow and zero-poll terminal reconciliation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from re_gpt.global_rate_limit import GlobalBackendRateLimiter
from re_gpt.mutation_service import MutationService
from re_gpt.storage import DEFAULT_DB_PATH


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [accept_stream_follow] {message}", flush=True)


def run_acceptance(
    service: MutationService | Any,
    *,
    conversation_id: str,
    nonce: str,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    follow_timeout: float = 180.0,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    progress: list[dict[str, Any]] = []
    started = time.monotonic()

    def on_progress(event: dict[str, Any]) -> None:
        receipt = {
            "kind": event.get("kind"),
            "at_seconds": round(time.monotonic() - started, 6),
            "delta_chars": len(str(event.get("delta") or "")),
            "recipient": event.get("recipient"),
            "content_type": event.get("content_type"),
            "reason": event.get("reason"),
        }
        progress.append(receipt)
        _log(
            f"progress kind={receipt['kind']} delta_chars={receipt['delta_chars']} "
            f"recipient={receipt['recipient']}"
        )

    _log(f"submitting stream-follow probe to conversation={conversation_id}")
    result = service.continue_thread(
        conversation_id,
        f"Reply with only STREAM-{nonce}",
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        write_transport="async",
        dom_fallback=False,
        follow=True,
        follow_timeout=follow_timeout,
        progress_callback=on_progress,
    )
    returned_at = round(time.monotonic() - started, 6)
    nonterminal = [item for item in progress if item.get("kind") != "terminal"]
    terminal = [item for item in progress if item.get("kind") == "terminal"]
    checks = {
        "canonical_complete": result.get("state") == "complete",
        "stream_terminal_observed": bool(result.get("stream_terminal_observed")),
        "terminal_event_callback_seen": bool(terminal),
        "realtime_progress_seen": bool(nonterminal) and all(float(item["at_seconds"]) <= returned_at for item in nonterminal),
        "zero_poll": result.get("reconcile_poll_count") == 0,
        "single_terminal_reconcile": int(result.get("terminal_reconcile_count") or 0) == 1,
        "stream_follow_mode": result.get("follow_mode") == "stream_terminal_single_reconcile",
    }
    queue = GlobalBackendRateLimiter(db_path).snapshot()
    all_ok = all(checks.values())
    _log(f"completed state={result.get('state')} all_ok={all_ok} progress_events={len(progress)}")
    return {
        "schema": "re-gpt.stream-follow.acceptance.v1",
        "nonce": nonce,
        "conversation_id": conversation_id,
        "elapsed_seconds": returned_at,
        "checks": checks,
        "all_ok": all_ok,
        "progress": progress,
        "stream_summary": {
            "terminal_observed": result.get("stream_terminal_observed"),
            "terminal_reason": result.get("stream_terminal_reason"),
            "event_counts": result.get("stream_event_counts") or {},
            "follow_mode": result.get("follow_mode"),
            "terminal_reconcile_count": result.get("terminal_reconcile_count"),
            "reconcile_poll_count": result.get("reconcile_poll_count"),
        },
        "canonical": {
            "state": result.get("state"),
            "operation_id": result.get("operation_id"),
            "user_node_id": result.get("user_node_id"),
            "assistant_node_id": result.get("assistant_node_id"),
            "read_adapter": result.get("read_adapter"),
        },
        "backend_queue": queue,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify real-time SSE progress with one terminal reconciliation and zero polling")
    parser.add_argument("conversation_id")
    parser.add_argument("--nonce")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--follow-timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    nonce = str(args.nonce or time.time_ns())
    service = MutationService(db_path=args.db_path)
    result = run_acceptance(
        service,
        conversation_id=args.conversation_id,
        nonce=nonce,
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        browser_timeout=args.browser_timeout,
        follow_timeout=args.follow_timeout,
        db_path=args.db_path,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result.get("all_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
