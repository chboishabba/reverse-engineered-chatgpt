#!/usr/bin/env python3
"""Live sacrificial acceptance matrix for direct API attachments."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import tempfile
import time
from typing import Any

from re_gpt.mutation_service import MutationService
from re_gpt.storage import DEFAULT_DB_PATH

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def _summary(name: str, expected_count: int, result: dict[str, Any]) -> dict[str, Any]:
    ids = list(result.get("attachment_ids") or [])
    return {
        "name": name,
        "state": result.get("state"),
        "operation_id": result.get("operation_id"),
        "conversation_id": result.get("conversation_id"),
        "user_node_id": result.get("user_node_id"),
        "assistant_node_id": result.get("assistant_node_id"),
        "transport": result.get("transport"),
        "read_adapter": result.get("read_adapter"),
        "expected_attachment_count": expected_count,
        "attachment_ids": ids,
        "attachment_count_ok": len(ids) == expected_count and len(ids) == len(set(ids)),
    }


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [accept_attachment_matrix] {message}", flush=True)


def run_acceptance(
    service: MutationService | Any,
    *,
    nonce: str,
    work_dir: Path,
    large_bytes: int = 512 * 1024,
    step_delay: float = 15.0,
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
    created = service.new_conversation(f"Reply with only ATTACH-ROOT-{nonce}", **common)
    conversation_id = str(created.get("conversation_id") or "")
    results: list[dict[str, Any]] = [{
        "name": "new_conversation",
        "state": created.get("state"),
        "conversation_id": conversation_id or None,
    }]
    if created.get("state") != "complete" or not conversation_id:
        return {"nonce": nonce, "conversation_id": conversation_id or None, "all_complete": False, "operations": results}

    multi_a = work_dir / "multi-a.txt"
    multi_b = work_dir / "multi-b.txt"
    multi_a.write_text(f"alpha-{nonce}\n", encoding="utf-8")
    multi_b.write_text(f"beta-{nonce}\n", encoding="utf-8")

    dup_a = work_dir / "dup-a" / "same.txt"
    dup_b = work_dir / "dup-b" / "same.txt"
    dup_a.parent.mkdir(parents=True, exist_ok=True)
    dup_b.parent.mkdir(parents=True, exist_ok=True)
    dup_a.write_text(f"duplicate-a-{nonce}\n", encoding="utf-8")
    dup_b.write_text(f"duplicate-b-{nonce}-different-size\n", encoding="utf-8")

    large = work_dir / "large.bin"
    large.write_bytes((nonce.encode("utf-8") + b"\0") * max(1, large_bytes // (len(nonce.encode("utf-8")) + 1)))
    if large.stat().st_size < large_bytes:
        with large.open("ab") as handle:
            handle.write(b"x" * (large_bytes - large.stat().st_size))

    png = work_dir / "pixel.png"
    png.write_bytes(_ONE_PIXEL_PNG)

    cases = [
        ("multi_file", [multi_a, multi_b]),
        ("duplicate_basename", [dup_a, dup_b]),
        ("larger_binary", [large]),
        ("non_text_png", [png]),
    ]

    for index, (name, attachments) in enumerate(cases, start=1):
        if step_delay > 0:
            _log(f"Pausing {step_delay:.1f}s before {name}...")
            time.sleep(step_delay)
        _log(f"Case {index}/{len(cases)}: {name} ({len(attachments)} attachment(s))")
        result = service.continue_thread(
            conversation_id,
            f"Reply with only ATTACH-{name.upper()}-{nonce}",
            attachments=attachments,
            write_transport="async",
            dom_fallback=False,
            **common,
        )
        summary = _summary(name, len(attachments), result)
        results.append(summary)
        _log(f"{name}: state={summary['state']} attachment_ids={summary['attachment_ids']}")
        if summary["state"] != "complete" or not summary["attachment_count_ok"]:
            return {"nonce": nonce, "conversation_id": conversation_id, "all_complete": False, "operations": results}

    return {
        "nonce": nonce,
        "conversation_id": conversation_id,
        "all_complete": True,
        "large_bytes": large_bytes,
        "operations": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify direct attachment robustness across multi-file, duplicate-name, large and binary cases")
    parser.add_argument("--nonce")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--follow-timeout", type=float, default=120.0)
    parser.add_argument("--step-delay", type=float, default=15.0)
    parser.add_argument("--large-bytes", type=int, default=512 * 1024)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    nonce = str(args.nonce or time.time_ns())
    service = MutationService(db_path=args.db_path)
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        result = run_acceptance(
            service,
            nonce=nonce,
            work_dir=args.work_dir,
            large_bytes=args.large_bytes,
            step_delay=args.step_delay,
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            browser_timeout=args.browser_timeout,
            follow_timeout=args.follow_timeout,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="re-gpt-attachment-matrix-") as raw:
            result = run_acceptance(
                service,
                nonce=nonce,
                work_dir=Path(raw),
                large_bytes=args.large_bytes,
                step_delay=args.step_delay,
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
