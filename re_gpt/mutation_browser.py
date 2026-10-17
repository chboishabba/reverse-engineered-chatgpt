"""Production browser-owned mutation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from chatgpt_dom_relay import DEFAULT_ALIAS_PATHS, DEFAULT_PROFILE_DIR, load_alias_map, resolve_prompt_text, resolve_thread_selector
from .accessibility_browser import AccessibilityBrowserRelay, BROWSER_MODES
from .attachments import LocalAttachment, stage_attachments
from .browser_mutation import follow_prompt, submit_prompt_once
from .browser_protocol_recorder import BrowserProtocolRecorder
from .browser_readiness import open_ready_thread


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DOM-only browser-owned ChatGPT mutation runner")
    parser.add_argument("selector", help="Conversation UUID, URL, or alias")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--attach", action="append", type=Path, default=[], help="Attach a file; repeat for multiple files")
    parser.add_argument("--alias-file", action="append", type=Path, default=[])
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=60, help="Per browser readiness/attachment/follow budget in seconds")
    parser.add_argument("--idle-ms", type=int, default=2500)
    parser.add_argument("--follow", action="store_true", help="Wait for a settled assistant response")
    parser.add_argument("--json", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    aliases = load_alias_map([*DEFAULT_ALIAS_PATHS, *args.alias_file])
    thread_id = resolve_thread_selector(args.selector, aliases)
    stdin_text = None if sys.stdin.isatty() else sys.stdin.read()
    prompt = resolve_prompt_text(prompt=args.prompt, prompt_file=args.prompt_file, stdin_text=stdin_text, stdin_is_tty=sys.stdin.isatty())
    if prompt is None or not prompt.strip():
        raise ValueError("provide --prompt, --prompt-file, or pipe prompt text on stdin")
    attachments = [LocalAttachment.from_path(path) for path in args.attach]

    stage_times: dict[str, float] = {"browser_start": time.time()}
    async with AccessibilityBrowserRelay(
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        timeout_ms=args.timeout * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        stage_times["browser_ready"] = time.time()
        async with BrowserProtocolRecorder(
            relay.page,
            operation="continue",
            save_session_snapshot=False,
        ) as recorder:
            readiness = await open_ready_thread(relay, thread_id)
            stage_times["authenticated"] = time.time()
            stage_times["hydrated"] = stage_times["authenticated"]

            attachment_receipts = []
            if attachments:
                stage_times["attachment_selecting"] = time.time()
                attachment_receipts = await stage_attachments(relay, attachments)
                stage_times["attachment_ready"] = time.time()

            result = await submit_prompt_once(relay, prompt)
            stage_times["submission_observed"] = time.time()
            if args.follow:
                try:
                    followed = await follow_prompt(relay, prompt, int(result["baseline_prompt_count"]))
                    result["latest_assistant"] = str(followed.get("latest_assistant") or "")
                    result["mutation_state"] = "assistant_observed"
                    stage_times["assistant_complete"] = time.time()
                except TimeoutError:
                    result["mutation_state"] = "submission_observed"
        result.update({
            "transport": "dom",
            "browser_mode": "attached" if args.cdp_endpoint else args.browser_mode,
            "resolved_thread_id": thread_id,
            "authenticated": readiness.authenticated,
            "hydrated": readiness.history_hydrated,
            "attachment_receipts": [asdict(item) for item in attachment_receipts],
            "attachments": [
                {"path": str(item.path), "filename": item.filename, "mime_type": item.mime_type, "size": item.size, "sha256": item.sha256}
                for item in attachments
            ],
            "protocol_observations": recorder.to_dicts(),
            "stage_times": stage_times,
        })
        return result


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        latest = str(result.get("latest_assistant") or "").strip()
        print(latest or str(result.get("mutation_state") or "submission_observed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
