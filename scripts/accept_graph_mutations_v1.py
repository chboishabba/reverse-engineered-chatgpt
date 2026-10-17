#!/usr/bin/env python3
"""One-shot live acceptance sequence for the direct graph mutation surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from re_gpt.mutation_service import MutationService
from re_gpt.storage import DEFAULT_DB_PATH


def _summary(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "state": result.get("state"),
        "operation_id": result.get("operation_id"),
        "conversation_id": result.get("conversation_id"),
        "transport": result.get("transport"),
        "read_adapter": result.get("read_adapter"),
        "user_node_id": result.get("user_node_id"),
        "assistant_node_id": result.get("assistant_node_id"),
        "assistant_text": result.get("assistant_text"),
        "attachment_ids": list(result.get("attachment_ids") or []),
        "write_stages": [
            item.get("bootstrap_stage")
            for item in (result.get("write_observations") or [])
            if isinstance(item, dict)
        ],
    }


def run_acceptance(
    service: MutationService | Any,
    *,
    nonce: str,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    follow_timeout: float = 120.0,
) -> dict[str, Any]:
    common = {
        "profile_dir": profile_dir,
        "browser_mode": browser_mode,
        "cdp_endpoint": cdp_endpoint,
        "browser_timeout": browser_timeout,
        "follow": True,
        "follow_timeout": follow_timeout,
    }
    operations: list[dict[str, Any]] = []

    created = service.new_conversation(f"Reply with only NEW-{nonce}", **common)
    operations.append(_summary("new_conversation", created))
    if created.get("state") != "complete":
        return {"nonce": nonce, "conversation_id": created.get("conversation_id"), "all_complete": False, "operations": operations}

    conversation_id = str(created.get("conversation_id") or "")
    original_user = str(created.get("user_node_id") or "")
    original_assistant = str(created.get("assistant_node_id") or "")
    if not (conversation_id and original_user and original_assistant):
        return {"nonce": nonce, "conversation_id": conversation_id or None, "all_complete": False, "operations": operations}

    branched = service.branch_from_node(
        conversation_id,
        original_assistant,
        f"Reply with only BRANCH-{nonce}",
        **common,
    )
    operations.append(_summary("branch", branched))
    if branched.get("state") != "complete":
        return {"nonce": nonce, "conversation_id": conversation_id, "all_complete": False, "operations": operations}

    regenerated = service.regenerate(conversation_id, original_user, **common)
    operations.append(_summary("regenerate", regenerated))
    if regenerated.get("state") != "complete":
        return {"nonce": nonce, "conversation_id": conversation_id, "all_complete": False, "operations": operations}

    edited = service.edit_and_branch(
        conversation_id,
        original_user,
        f"Reply with only EDIT-{nonce}",
        **common,
    )
    operations.append(_summary("edit_and_branch", edited))

    return {
        "nonce": nonce,
        "conversation_id": conversation_id,
        "all_complete": all(item.get("state") == "complete" for item in operations),
        "operations": operations,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify direct new/branch/regenerate/edit graph mutations in one sacrificial conversation")
    parser.add_argument("--nonce", default=None, help="Unique receipt token; defaults to time_ns")
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--follow-timeout", type=float, default=120.0)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    nonce = str(args.nonce or time.time_ns())
    service = MutationService(db_path=args.db_path)
    result = run_acceptance(
        service,
        nonce=nonce,
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        browser_timeout=args.browser_timeout,
        follow_timeout=args.follow_timeout,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result.get("all_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
