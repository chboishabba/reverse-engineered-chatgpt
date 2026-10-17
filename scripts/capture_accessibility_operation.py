#!/usr/bin/env python3
"""Run one bounded accessibility capture and emit a share-safe JSON receipt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from chatgpt_dom_relay import DEFAULT_PROFILE_DIR
from re_gpt.accessibility_browser import BROWSER_MODES
from re_gpt.accessibility_capture import capture_attachment_continue
from re_gpt.experiment_receipt import serialize_experiment_receipt
from re_gpt.storage import DEFAULT_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one bounded ChatGPT accessibility operation with redacted protocol and graph receipts."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    attachment = subparsers.add_parser(
        "attachment-continue",
        help="Send one prompt with one or more files through the existing accessibility browser path.",
    )
    attachment.add_argument("selector", help="Existing conversation UUID, URL, or configured alias")
    prompt_group = attachment.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Prompt text for the bounded probe")
    prompt_group.add_argument("--prompt-file", type=Path, help="UTF-8 file containing the probe prompt")
    attachment.add_argument(
        "--attach",
        action="append",
        type=Path,
        required=True,
        help="Attach a local file; repeat for multiple files",
    )
    attachment.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    attachment.add_argument("--browser-mode", choices=BROWSER_MODES, default="displayless")
    attachment.add_argument("--cdp-endpoint")
    attachment.add_argument("--timeout", type=int, default=60, help="Per browser/read budget in seconds")
    attachment.add_argument("--follow", action="store_true", help="Allow the browser actuator to wait for rendered assistant completion")
    attachment.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    attachment.add_argument("--output", type=Path, help="Write receipt JSON here instead of stdout")
    return parser


def _prompt_from_args(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        prompt = str(args.prompt)
    else:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is empty")
    return prompt


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prompt = _prompt_from_args(args)
        if args.operation != "attachment-continue":
            raise ValueError(f"unsupported operation: {args.operation}")
        receipt = capture_attachment_continue(
            args.selector,
            prompt,
            args.attach,
            db_path=args.db_path,
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            browser_timeout=args.timeout,
            follow=args.follow,
        )
        rendered = serialize_experiment_receipt(receipt)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - failures here are pre-receipt/pre-actuation boundaries.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
