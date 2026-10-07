"""Accessibility-first browser-owned ChatGPT transport.

This module keeps authentication and mutation state inside a real browser while
allowing displayless operation.  The default mode uses Playwright's ``chromium``
channel, which opts into Chromium's modern unified headless implementation.  A
normal headed Chrome launch remains available as an explicit compatibility
fallback.

The transport deliberately reuses :class:`chatgpt_dom_relay.ChatGPTDomRelay`
for ChatGPT-specific protocol and DOM behavior.  It changes browser lifecycle
only; same-origin API submission, DOM submission, and reply detection stay in
one implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from chatgpt_dom_relay import (
    DEFAULT_ALIAS_PATHS,
    DEFAULT_PROFILE_DIR,
    ChatGPTDomRelay,
    clear_stale_profile_locks,
    load_alias_map,
    resolve_prompt_text,
    resolve_thread_selector,
)

BROWSER_MODES = ("headless", "headed")


class AccessibilityBrowserRelay(ChatGPTDomRelay):
    """ChatGPT relay with a displayless real-Chromium browser mode."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        browser_mode: str = "headless",
        timeout_ms: int,
        idle_ms: int,
    ) -> None:
        if browser_mode not in BROWSER_MODES:
            raise ValueError(
                f"unknown browser mode {browser_mode!r}; expected one of {BROWSER_MODES}"
            )
        self.browser_mode = browser_mode
        super().__init__(
            profile_dir=profile_dir,
            headed=browser_mode == "headed",
            timeout_ms=timeout_ms,
            idle_ms=idle_ms,
        )

    def launch_options(self) -> dict[str, Any]:
        """Return Playwright launch options for the selected browser mode."""

        headed = self.browser_mode == "headed"
        return {
            "headless": not headed,
            # Playwright documents channel="chromium" as opting into modern
            # unified headless, i.e. the regular Chromium browser rather than
            # chromium-headless-shell.  Keep branded Chrome for the explicit
            # headed compatibility path so existing browser profiles continue
            # to behave as before.
            "channel": "chrome" if headed else "chromium",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                *(["--start-minimized"] if headed else []),
            ],
            "viewport": {"width": 1440, "height": 960},
        }

    async def __aenter__(self) -> "AccessibilityBrowserRelay":
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        clear_stale_profile_locks(self.profile_dir)
        self._playwright = await async_playwright().start()
        options = self.launch_options()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            **options,
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        return self


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Accessibility-first ChatGPT relay using a browser-owned session. "
            "Defaults to Playwright unified Chromium headless and falls back "
            "from same-origin API submission to the real composer DOM."
        )
    )
    parser.add_argument(
        "selector",
        help="Conversation UUID, full ChatGPT URL, or alias name.",
    )
    parser.add_argument("--prompt", help="Prompt text to submit.")
    parser.add_argument("--prompt-file", type=Path, help="Read prompt text from a file.")
    parser.add_argument(
        "--alias-file",
        action="append",
        type=Path,
        default=[],
        help="Additional JSON alias file mapping short names to conversation IDs/URLs.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="Persistent browser profile directory (default: %(default)s).",
    )
    parser.add_argument(
        "--browser-mode",
        choices=BROWSER_MODES,
        default="headless",
        help=(
            "Browser lifecycle mode. 'headless' uses Playwright's unified Chromium "
            "headless implementation; 'headed' uses installed Chrome (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Compatibility alias for --browser-mode headed.",
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "api", "dom"),
        default="auto",
        help=(
            "Mutation transport. auto tries browser same-origin API first and then "
            "the composer DOM (default: %(default)s)."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of reply text.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Overall browser timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--idle-ms",
        type=int,
        default=2500,
        help="Reply stability window in milliseconds (default: %(default)s).",
    )
    return parser


def resolve_browser_mode(args: argparse.Namespace) -> str:
    return "headed" if getattr(args, "headed", False) else args.browser_mode


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    alias_map = load_alias_map([*DEFAULT_ALIAS_PATHS, *args.alias_file])
    thread_id = resolve_thread_selector(args.selector, alias_map)
    stdin_text = None if sys.stdin.isatty() else sys.stdin.read()
    prompt = resolve_prompt_text(
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        stdin_text=stdin_text,
        stdin_is_tty=sys.stdin.isatty(),
    )
    if prompt is None or not prompt.strip():
        raise ValueError("Provide --prompt, --prompt-file, or pipe prompt text on stdin.")

    browser_mode = resolve_browser_mode(args)
    failures: list[tuple[str, str]] = []

    async with AccessibilityBrowserRelay(
        profile_dir=args.profile_dir,
        browser_mode=browser_mode,
        timeout_ms=args.timeout * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        transports = ("api", "dom") if args.transport == "auto" else (args.transport,)
        for transport in transports:
            try:
                if transport == "api":
                    await relay.open_api_context()
                    result = await relay.send_api_prompt(prompt, thread_id)
                else:
                    await relay.open_thread(thread_id)
                    result = await relay.send_prompt(prompt)
            except Exception as exc:  # noqa: BLE001 - fallback is intentional.
                failures.append((transport, str(exc)))
                continue

            result["transport"] = transport
            result["browser_mode"] = browser_mode
            result["resolved_thread_id"] = thread_id
            if failures:
                result["fallback_from"] = failures[-1][0]
            return result

    details = "; ".join(f"{transport}: {detail}" for transport, detail in failures)
    raise RuntimeError(f"all requested browser transports failed ({details})")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
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
        print(str(result.get("latest_assistant") or "").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
