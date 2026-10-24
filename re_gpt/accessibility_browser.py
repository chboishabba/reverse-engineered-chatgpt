"""Accessibility-first browser-owned ChatGPT transport.

The relay can either launch a browser or attach to an already-running
Chromium/Chrome instance over CDP. Launch mode supports normal Chrome on
Chromium's displayless Ozone backend, Playwright unified Chromium headless,
and a conventional headed Chrome fallback. Attach mode reuses an existing
browser-owned authenticated session and never closes that browser or its
contexts when the relay exits.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
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

BROWSER_MODES = ("displayless", "headless", "headed")
_EXPLICIT_API_FAILURE_RE = re.compile(
    r"^API (auth-session|conversation-get|sentinel|conversation-post) "
    r"failed with HTTP \d+:"
)


def api_failure_allows_dom_fallback(exc: BaseException) -> bool:
    """Return whether an API submission failure is explicit enough to retry via DOM.

    ``send_api_prompt`` reports server-declared non-success responses with a
    stable ``API <stage> failed with HTTP ...`` message. Those responses prove
    the browser received a rejection. By contrast, a Playwright/CDP timeout,
    page close, transport reset, or evaluation failure can happen after the
    conversation POST was accepted; its mutation outcome is ambiguous and a
    second DOM submission would risk duplicating the user's prompt.
    """

    return bool(_EXPLICIT_API_FAILURE_RE.match(str(exc)))


class AmbiguousBrowserMutationError(RuntimeError):
    """Raised when a browser API mutation may have committed remotely."""


class AccessibilityBrowserRelay(ChatGPTDomRelay):
    """ChatGPT relay with launched and non-owning attached browser modes."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        timeout_ms: int,
        idle_ms: int,
    ) -> None:
        if browser_mode not in BROWSER_MODES:
            raise ValueError(
                f"unknown browser mode {browser_mode!r}; expected one of {BROWSER_MODES}"
            )
        self.browser_mode = browser_mode
        self.cdp_endpoint = cdp_endpoint.strip() if cdp_endpoint else None
        self._attached_browser = None
        self._attached = bool(self.cdp_endpoint)
        super().__init__(
            profile_dir=profile_dir,
            headed=browser_mode == "headed",
            timeout_ms=timeout_ms,
            idle_ms=idle_ms,
        )

    @property
    def attaches_existing_browser(self) -> bool:
        return self._attached

    def launch_options(self) -> dict[str, Any]:
        """Return Playwright launch options for a relay-owned browser."""

        base_args = [
            "--no-first-run",
            "--no-default-browser-check",
        ]

        if self.browser_mode == "displayless":
            env = dict(os.environ)
            env.pop("DISPLAY", None)
            return {
                "headless": False,
                "channel": "chrome",
                "args": [
                    *base_args,
                    "--ozone-platform=headless",
                    "--ozone-override-screen-size=1440,960",
                ],
                "env": env,
                "viewport": {"width": 1440, "height": 960},
            }

        if self.browser_mode == "headless":
            return {
                "headless": True,
                "channel": "chromium",
                "args": base_args,
                "viewport": {"width": 1440, "height": 960},
            }

        return {
            "headless": False,
            "channel": "chrome",
            "args": [*base_args, "--start-minimized"],
            "viewport": {"width": 1440, "height": 960},
        }

    async def __aenter__(self) -> "AccessibilityBrowserRelay":
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        if self.cdp_endpoint:
            self._log(f"attaching to existing browser at {self.cdp_endpoint}")
            try:
                self._attached_browser = await self._playwright.chromium.connect_over_cdp(
                    self.cdp_endpoint,
                    timeout=self.timeout_ms,
                )
                contexts = self._attached_browser.contexts
                if not contexts:
                    raise RuntimeError(
                        "CDP browser has no browser context; open Chrome with a normal profile first."
                    )

                selected_context = contexts[0]
                selected_page = None
                for context in contexts:
                    for page in context.pages:
                        if "chatgpt.com" in (page.url or ""):
                            selected_context = context
                            selected_page = page
                            break
                    if selected_page is not None:
                        break

                self._context = selected_context
                self._page = selected_page or await selected_context.new_page()
                return self
            except Exception:
                if self._playwright is not None:
                    with contextlib.suppress(Exception):
                        await self._playwright.stop()
                raise

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        clear_stale_profile_locks(self.profile_dir)
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

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.cdp_endpoint:
            # Non-owning attach: disconnect Playwright only. Closing the context
            # would close the user's already-running authenticated browser tabs.
            if self._playwright is not None:
                with contextlib.suppress(Exception):
                    await self._playwright.stop()
            return
        await super().__aexit__(exc_type, exc, tb)

    async def open_api_context(self) -> None:
        """Prepare a browser-owned API context without disturbing an attached tab."""

        # An attached browser belongs to the operator.  Do not replace its
        # selected conversation with the ChatGPT root merely to perform an
        # exact read: that is disruptive and creates a fresh request context,
        # defeating reuse of an already-admitted browser session.
        if self.attaches_existing_browser:
            current_url = self.page.url
            if "chatgpt.com" not in current_url:
                raise RuntimeError(
                    "Attached browser has no ChatGPT page. Open an authenticated "
                    "ChatGPT tab before using an attached exact-read adapter."
                )
            if "/auth/" in current_url or "/login" in current_url:
                raise RuntimeError("Attached browser is not authenticated for ChatGPT.")
            if "just a moment" in (await self.page.title()).lower():
                raise RuntimeError("Cloudflare challenge is still active in the attached browser.")
            self._log("reusing attached ChatGPT page for API context")
            return

        await super().open_api_context()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Accessibility-first ChatGPT relay using a browser-owned session. "
            "Launch normal Chrome on Ozone displayless by default, or attach to "
            "an already-running authenticated browser over CDP."
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
        help="Persistent browser profile directory used only when launching a browser.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=BROWSER_MODES,
        default="displayless",
        help=(
            "Launch mode: displayless = normal Chrome on Ozone off-screen backend; "
            "headless = unified Chromium headless; headed = normal Chrome window. "
            "Ignored with --cdp-endpoint."
        ),
    )
    parser.add_argument(
        "--cdp-endpoint",
        help=(
            "Attach to an already-running Chromium/Chrome browser over CDP, e.g. "
            "http://127.0.0.1:9222. Attached browsers are not closed by the relay."
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Compatibility alias for --browser-mode headed when launching.",
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "api", "dom"),
        default="auto",
        help=(
            "Mutation transport. auto tries browser same-origin API first and then "
            "the composer DOM only after an explicit API rejection (default: %(default)s)."
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
        cdp_endpoint=args.cdp_endpoint,
        timeout_ms=args.timeout * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        if args.transport in {"api", "auto"}:
            try:
                await relay.open_api_context()
            except Exception as exc:  # No mutation has been attempted yet.
                failures.append(("api-context", str(exc)))
                if args.transport == "api":
                    raise
            else:
                try:
                    result = await relay.send_api_prompt(prompt, thread_id)
                except Exception as exc:  # noqa: BLE001
                    failures.append(("api", str(exc)))
                    if args.transport == "api":
                        raise
                    if not api_failure_allows_dom_fallback(exc):
                        raise AmbiguousBrowserMutationError(
                            "browser API submission outcome is ambiguous; refusing DOM "
                            "fallback to avoid a duplicate prompt. Reconcile/pull the "
                            "conversation before deciding whether to retry."
                        ) from exc
                else:
                    result["transport"] = "api"
                    result["browser_mode"] = (
                        "attached" if args.cdp_endpoint else browser_mode
                    )
                    result["resolved_thread_id"] = thread_id
                    if failures:
                        result["fallback_from"] = failures[-1][0]
                    return result

        if args.transport in {"dom", "auto"}:
            try:
                await relay.open_thread(thread_id)
                result = await relay.send_prompt(prompt)
            except Exception as exc:  # noqa: BLE001
                failures.append(("dom", str(exc)))
            else:
                result["transport"] = "dom"
                result["browser_mode"] = (
                    "attached" if args.cdp_endpoint else browser_mode
                )
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
