"""Production browser-owned mutation runner.

Unlike the historical accessibility relay, this entrypoint never tries the
handcrafted ChatGPT API mutation protocol.  It waits for authenticated thread
hydration, submits once through the real DOM, and returns after browser
submission evidence unless ``--follow`` is explicitly requested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from chatgpt_dom_relay import DEFAULT_ALIAS_PATHS, DEFAULT_PROFILE_DIR, load_alias_map, resolve_prompt_text, resolve_thread_selector
from .accessibility_browser import AccessibilityBrowserRelay, BROWSER_MODES
from .browser_mutation import follow_prompt, submit_prompt_once
from .browser_readiness import open_ready_thread


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DOM-only browser-owned ChatGPT mutation runner")
    parser.add_argument("selector", help="Conversation UUID, URL, or alias")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--alias-file", action="append", type=Path, default=[])
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=60, help="Per browser readiness/follow budget in seconds")
    parser.add_argument("--idle-ms", type=int, default=2500)
    parser.add_argument("--follow", action="store_true", help="Wait for a settled assistant response")
    parser.add_argument("--json", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    aliases = load_alias_map([*DEFAULT_ALIAS_PATHS, *args.alias_file])
    thread_id = resolve_thread_selector(args.selector, aliases)
    stdin_text = None if sys.stdin.isatty() else sys.stdin.read()
    prompt = resolve_prompt_text(
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        stdin_text=stdin_text,
        stdin_is_tty=sys.stdin.isatty(),
    )
    if prompt is None or not prompt.strip():
        raise ValueError("provide --prompt, --prompt-file, or pipe prompt text on stdin")

    launched_at = time.time()
    stage_times: dict[str, float] = {"browser_start": launched_at}
    async with AccessibilityBrowserRelay(
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        timeout_ms=args.timeout * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        stage_times["browser_ready"] = time.time()
        readiness = await open_ready_thread(relay, thread_id)
        stage_times["authenticated"] = time.time()
        stage_times["hydrated"] = stage_times["authenticated"]
        result = await submit_prompt_once(relay, prompt)
        stage_times["submission_observed"] = time.time()
        result.update(
            {
                "transport": "dom",
                "browser_mode": "attached" if args.cdp_endpoint else args.browser_mode,
                "resolved_thread_id": thread_id,
                "authenticated": readiness.authenticated,
                "hydrated": readiness.history_hydrated,
                "stage_times": stage_times,
            }
        )
        if args.follow:
            followed = await follow_prompt(relay, prompt, int(result["baseline_prompt_count"]))
            result["latest_assistant"] = str(followed.get("latest_assistant") or "")
            result["mutation_state"] = "assistant_observed"
            stage_times["assistant_complete"] = time.time()
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
        if latest:
            print(latest)
        else:
            print(str(result.get("mutation_state") or "submission_observed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
