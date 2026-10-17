#!/usr/bin/env python3
"""Opt-in live acceptance matrix for existing-thread re-gpt + MCP v1.

This script performs real browser mutations. It is intentionally not part of
the unit test suite and must be supplied disposable authenticated threads.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from re_gpt.mutation_service import MutationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run live short/medium/long existing-thread ACK acceptance plus one attachment"
    )
    parser.add_argument("--short-thread", required=True)
    parser.add_argument("--medium-thread", required=True)
    parser.add_argument("--long-thread", required=True)
    parser.add_argument("--attachment", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--follow-timeout", type=float, default=120.0)
    parser.add_argument("--db-path", type=Path)
    return parser


def _probe(
    service: MutationService,
    label: str,
    conversation_id: str,
    *,
    attachment: Path | None,
    profile_dir: Path | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
    follow_timeout: float,
) -> dict[str, Any]:
    nonce = f"REGPT-V1-{label.upper()}-{time.time_ns()}"
    prompt = f"Reply exactly {nonce}."
    started = time.perf_counter()
    result = service.continue_thread(
        conversation_id,
        prompt,
        attachments=[attachment] if attachment is not None else (),
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        follow=True,
        follow_timeout=follow_timeout,
    )
    elapsed = round(time.perf_counter() - started, 3)
    assistant_text = str(result.get("assistant_text") or "").strip()
    expected_reply = f"{nonce}."
    attachment_ids = list(result.get("attachment_ids") or [])
    accepted = (
        result.get("state") == "complete"
        and assistant_text == expected_reply
        and (attachment is None or bool(attachment_ids))
    )
    return {
        "label": label,
        "conversation_id": conversation_id,
        "operation_id": result.get("operation_id"),
        "state": result.get("state"),
        "read_adapter": result.get("read_adapter"),
        "user_node_id": result.get("user_node_id"),
        "assistant_node_id": result.get("assistant_node_id"),
        "assistant_text": assistant_text,
        "expected_reply": expected_reply,
        "attachment_ids": attachment_ids,
        "elapsed_seconds": elapsed,
        "accepted": accepted,
        "error_class": result.get("error_class"),
        "error_detail": result.get("error_detail"),
    }


def main() -> int:
    args = build_parser().parse_args()
    attachment = args.attachment.expanduser().resolve()
    if not attachment.is_file():
        raise SystemExit(f"attachment does not exist or is not a file: {attachment}")

    service_kwargs: dict[str, Any] = {}
    if args.db_path is not None:
        service_kwargs["db_path"] = args.db_path.expanduser()
    service = MutationService(**service_kwargs)

    common = {
        "profile_dir": args.profile_dir,
        "browser_mode": args.browser_mode,
        "cdp_endpoint": args.cdp_endpoint,
        "browser_timeout": args.browser_timeout,
        "follow_timeout": args.follow_timeout,
    }
    receipts = [
        _probe(service, "short", args.short_thread, attachment=None, **common),
        _probe(service, "medium", args.medium_thread, attachment=None, **common),
        _probe(service, "long", args.long_thread, attachment=None, **common),
        _probe(service, "attachment", args.short_thread, attachment=attachment, **common),
    ]
    payload = {
        "accepted": all(receipt["accepted"] for receipt in receipts),
        "receipts": receipts,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
