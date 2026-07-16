#!/usr/bin/env python3
"""Drive a real ChatGPT conversation through the browser DOM.

This avoids the blocked backend POST path by using a persistent Chromium
profile, opening the target conversation page, typing into the real composer,
and waiting for the assistant response to finish in the page.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = ROOT / "chatgpt_browser_profile"
DEFAULT_ALIAS_PATHS = (
    ROOT / "chatgpt_threads.json",
    Path.home() / ".chatgpt_threads.json",
)
UUID_RE = re.compile(
    r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
PROFILE_LOCK_PID_RE = re.compile(r"-(?P<pid>\d+)$")
COMPOSER_SELECTORS = (
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    '[data-testid="composer-root"] div[contenteditable="true"]',
    'textarea[placeholder]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"][translate="no"]',
)
EDITOR_TEXT_SELECTORS = (
    "#prompt-textarea > p:nth-child(1)",
    "#prompt-textarea",
)
SEND_BUTTON_SELECTORS = (
    "#composer-submit-button",
    'button[data-testid="send-button"]',
    'button[aria-label*="Send"]',
    'form button[type="submit"]',
)
STOP_BUTTON_SELECTORS = (
    'button[data-testid="stop-button"]',
    'button[aria-label*="Stop"]',
)
TURN_SELECTOR = "[data-message-author-role]"


def extract_thread_id(selector: str) -> str | None:
    match = UUID_RE.search(selector.strip())
    if not match:
        return None
    return match.group("id").lower()


def load_alias_map(paths: list[Path]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Alias file must contain a JSON object: {path}")
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"Alias entries must be string:string pairs: {path}")
            thread_id = extract_thread_id(value)
            if not thread_id:
                raise ValueError(f"Alias value does not contain a ChatGPT thread id: {path}::{key}")
            aliases[key.strip().lower()] = thread_id
    return aliases


def resolve_thread_selector(selector: str, alias_map: dict[str, str]) -> str:
    selector = selector.strip()
    if not selector:
        raise ValueError("thread selector is empty")
    thread_id = extract_thread_id(selector)
    if thread_id:
        return thread_id
    alias = alias_map.get(selector.lower())
    if alias:
        return alias
    raise ValueError(
        "Unable to resolve thread selector. Pass a full ChatGPT conversation URL, "
        "a raw conversation UUID, or define the alias in chatgpt_threads.json."
    )


def resolve_prompt_text(
    *,
    prompt: str | None,
    prompt_file: Path | None,
    stdin_text: str | None,
    stdin_is_tty: bool,
) -> str | None:
    if prompt and prompt_file:
        raise ValueError("Provide either --prompt or --prompt-file, not both.")
    if prompt is not None:
        return prompt
    if prompt_file is not None:
        return prompt_file.read_text(encoding="utf-8")
    if not stdin_is_tty and stdin_text is not None:
        return stdin_text
    return None


def build_conversation_url(thread_id: str) -> str:
    normalized = extract_thread_id(thread_id)
    if not normalized:
        raise ValueError(f"Invalid conversation id: {thread_id}")
    return f"https://chatgpt.com/c/{normalized}"


def summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    turns = snapshot.get("turns") or []
    assistant_turns = [turn for turn in turns if turn.get("role") == "assistant"]
    user_turns = [turn for turn in turns if turn.get("role") == "user"]
    return {
        "conversation_id": snapshot.get("conversation_id"),
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "assistant_turn_count": len(assistant_turns),
        "user_turn_count": len(user_turns),
        "latest_assistant": assistant_turns[-1]["text"] if assistant_turns else "",
        "latest_user": user_turns[-1]["text"] if user_turns else "",
        "turns": turns,
    }


def assistant_reply_after_prompt(snapshot: dict[str, Any], prompt: str) -> str | None:
    """Return the first assistant turn after the prompt submitted by this relay."""
    turns = snapshot.get("turns") or []
    prompt_index = next(
        (
            index
            for index in range(len(turns) - 1, -1, -1)
            if turns[index].get("role") == "user"
            and str(turns[index].get("text") or "").strip() == prompt
        ),
        None,
    )
    if prompt_index is None:
        return None
    for turn in turns[prompt_index + 1 :]:
        if turn.get("role") == "assistant":
            text = str(turn.get("text") or "").strip()
            return text or None
    return None


def latest_assistant_from_sse(raw: str) -> str:
    latest = ""
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line[6:])
            message = event.get("message") or {}
            if (message.get("author") or {}).get("role") != "assistant":
                continue
            parts = (message.get("content") or {}).get("parts") or []
            if parts and isinstance(parts[0], str):
                latest = parts[0]
    return latest


def clear_stale_profile_locks(profile_dir: Path) -> None:
    """Remove Chromium singleton links only when their owner is gone."""
    lock_path = profile_dir / "SingletonLock"
    if not lock_path.is_symlink():
        return
    target = os.readlink(lock_path)
    match = PROFILE_LOCK_PID_RE.search(target)
    if not match:
        return
    pid = int(match.group("pid"))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
            with contextlib.suppress(FileNotFoundError):
                (profile_dir / name).unlink()
    except PermissionError as exc:
        raise RuntimeError(
            f"Chromium profile appears to be in use by PID {pid}; close that browser first."
        ) from exc


class ChatGPTDomRelay:
    def __init__(
        self,
        *,
        profile_dir: Path,
        headed: bool,
        timeout_ms: int,
        idle_ms: int,
    ) -> None:
        self.profile_dir = profile_dir
        self.headed = headed
        self.timeout_ms = timeout_ms
        self.idle_ms = idle_ms
        self._playwright = None
        self._context = None
        self._page = None
        self._composer = None

    @staticmethod
    def _log(message: str) -> None:
        print(f"[chatgpt-relay] {message}", file=sys.stderr, flush=True)

    async def __aenter__(self) -> "ChatGPTDomRelay":
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        clear_stale_profile_locks(self.profile_dir)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=not self.headed,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                *( ["--start-minimized"] if self.headed else [] ),
            ],
            viewport={"width": 1440, "height": 960},
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("relay page is not ready")
        return self._page

    async def open_thread(self, thread_id: str) -> None:
        url = build_conversation_url(thread_id)
        self._log(f"opening {url}")
        # A long conversation may keep loading long after the composer exists.
        # Returning at commit lets us interact with the real UI without waiting
        # for every historical message and asset to finish loading.
        await self.page.goto(url, wait_until="commit", timeout=self.timeout_ms)
        await self.page.wait_for_timeout(500)
        current_url = self.page.url
        if "/auth/" in current_url or "/login" in current_url:
            raise RuntimeError(
                "Browser profile is not authenticated for ChatGPT. "
                "Run setup_browser_profile.py first and log in."
            )
        if "just a moment" in (await self.page.title()).lower():
            raise RuntimeError(
                "Cloudflare challenge is still active in headless mode. "
                "Rerun with --headed using the ITIR-suite .venv; the relay starts "
                "that window minimized."
            )
        self._log("waiting for composer")
        await self._wait_for_composer()
        self._log("composer ready")

    async def open_api_context(self) -> None:
        self._log("opening lightweight ChatGPT API context")
        await self.page.goto("https://chatgpt.com/", wait_until="commit", timeout=self.timeout_ms)
        await self.page.wait_for_timeout(500)
        if "/auth/" in self.page.url or "/login" in self.page.url:
            raise RuntimeError("Browser profile is not authenticated for ChatGPT.")
        if "just a moment" in (await self.page.title()).lower():
            raise RuntimeError("Cloudflare challenge is still active; rerun with --headed.")

    async def snapshot(self) -> dict[str, Any]:
        return await self.page.evaluate(
            """(turnSelector) => {
                const title = (document.title || '').replace(/^ChatGPT\\s*-\\s*/, '').trim();
                const turns = Array.from(document.querySelectorAll(turnSelector))
                  .map((node, index) => ({
                    index,
                    role: node.getAttribute('data-message-author-role') || '',
                    text: (node.innerText || '').trim(),
                  }))
                  .filter((turn) => turn.role && turn.text);
                const url = window.location.href;
                const match = url.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
                return {
                  title,
                  url,
                  conversation_id: match ? match[0].toLowerCase() : null,
                  turns,
                };
            }""",
            TURN_SELECTOR,
        )

    async def wait_for_history(self) -> None:
        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            snapshot = summarize_snapshot(await self.snapshot())
            if snapshot["turns"]:
                return
            await self.page.wait_for_timeout(700)
        raise TimeoutError("Timed out waiting for conversation history to hydrate.")

    async def send_prompt(self, prompt: str) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        self._log("typing prompt")
        composer = await self._locate_visible(EDITOR_TEXT_SELECTORS)
        self._log("focusing composer")
        await composer.click()
        self._log("inserting prompt")
        await self.page.keyboard.insert_text(prompt)
        self._log("sending prompt")
        send_button = await self._wait_for_enabled_send_button()
        await send_button.click()
        self._log("waiting for assistant response")
        return await self._wait_for_assistant_turn(prompt)

    async def send_api_prompt(self, prompt: str, conversation_id: str) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        self._log("requesting sentinel token and conversation parent")
        result = await self.page.evaluate(
            """async ({prompt, conversationId, messageId}) => {
                const sessionResponse = await fetch('/api/auth/session');
                const sessionBody = await sessionResponse.text();
                if (!sessionResponse.ok) {
                  return {ok: false, stage: 'auth-session', status: sessionResponse.status,
                    body: sessionBody.slice(0, 2000)};
                }
                const session = JSON.parse(sessionBody);
                const accessToken = session.accessToken || '';
                if (!accessToken) {
                  return {ok: false, stage: 'auth-session', status: 401,
                    body: 'No access token in /api/auth/session response.'};
                }
                const authHeaders = {
                  Authorization: `Bearer ${accessToken}`,
                  Accept: 'application/json',
                };
                const conversationResponse = await fetch(
                  `/backend-api/conversation/${conversationId}`,
                  {headers: authHeaders},
                );
                const conversationBody = await conversationResponse.text();
                if (!conversationResponse.ok) {
                  return {ok: false, stage: 'conversation-get', status: conversationResponse.status,
                    body: conversationBody.slice(0, 2000)};
                }
                const conversation = JSON.parse(conversationBody);
                const sentinelResponse = await fetch('/backend-api/sentinel/chat-requirements', {
                  method: 'POST', headers: {...authHeaders, 'Content-Type': 'application/json'},
                });
                const sentinelBody = await sentinelResponse.text();
                if (!sentinelResponse.ok) {
                  return {ok: false, stage: 'sentinel', status: sentinelResponse.status,
                    body: sentinelBody.slice(0, 2000)};
                }
                const sentinel = JSON.parse(sentinelBody);
                const payload = {
                  action: 'next',
                  conversation_id: conversationId,
                  parent_message_id: conversation.current_node || 'client-created-root',
                  model: conversation.default_model_slug || 'auto',
                  messages: [{
                    id: messageId,
                    author: {role: 'user'},
                    content: {content_type: 'text', parts: [prompt]},
                    create_time: Date.now() / 1000,
                    metadata: {},
                  }],
                  conversation_mode: {kind: 'primary_assistant'},
                  supported_encodings: ['v1'],
                  supports_buffering: false,
                  timezone_offset_min: new Date().getTimezoneOffset(),
                };
                const response = await fetch('/backend-api/conversation', {
                  method: 'POST',
                  headers: {
                    ...authHeaders,
                    'Content-Type': 'application/json',
                    Accept: 'text/event-stream',
                    'openai-sentinel-chat-requirements-token': sentinel.token || '',
                  },
                  body: JSON.stringify(payload),
                });
                const body = await response.text();
                return {ok: response.ok, stage: 'conversation-post', status: response.status, body};
            }""",
            {"prompt": prompt, "conversationId": conversation_id, "messageId": str(uuid.uuid4())},
        )
        if not result.get("ok"):
            raise RuntimeError(
                f"API {result.get('stage')} failed with HTTP {result.get('status')}: "
                f"{result.get('body', '')}"
            )
        raw = str(result.get("body") or "")
        return {
            "conversation_id": conversation_id,
            "latest_assistant": latest_assistant_from_sse(raw),
            "raw_sse": raw,
            "transport": "api",
        }

    async def _wait_for_composer(self) -> None:
        self._composer = await self._locate_visible(COMPOSER_SELECTORS)

    async def _locate_visible(self, selectors: tuple[str, ...]):
        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            for selector in selectors:
                # ChatGPT keeps hidden fallback textareas in the DOM. Select
                # only the rendered control rather than the first DOM match.
                locator = self.page.locator(f"{selector}:visible").first
                try:
                    if await locator.count():
                        return locator
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    continue
            await self.page.wait_for_timeout(250)
        if last_error:
            raise RuntimeError(f"Unable to locate a visible composer/send control: {last_error}")
        raise RuntimeError("Unable to locate a visible composer/send control.")

    async def _wait_for_enabled_send_button(self):
        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            for selector in SEND_BUTTON_SELECTORS:
                locator = self.page.locator(f"{selector}:visible").first
                try:
                    if await locator.count() and await locator.is_enabled():
                        return locator
                except Exception:
                    continue
            await self.page.wait_for_timeout(250)
        raise TimeoutError("Timed out waiting for the composer submit button to become enabled.")

    async def _wait_for_assistant_turn(self, prompt: str) -> dict[str, Any]:
        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        latest: dict[str, Any] | None = None
        latest_signature = ""
        stable_since: float | None = None

        while time.monotonic() < deadline:
            snapshot = summarize_snapshot(await self.snapshot())
            reply = assistant_reply_after_prompt(snapshot, prompt)
            if reply is not None:
                latest = snapshot
                latest["latest_assistant"] = reply
                signature = reply[-400:]
                if signature != latest_signature:
                    latest_signature = signature
                    stable_since = time.monotonic()
                elif stable_since is not None and (time.monotonic() - stable_since) * 1000 >= self.idle_ms:
                    # ChatGPT's composer labels vary between releases. A
                    # stable assistant turn with no visible stop control is
                    # sufficient even when the send button is renamed or
                    # temporarily disabled by the page.
                    if await self._send_button_ready() or not await self._stop_button_visible():
                        return latest
            await self.page.wait_for_timeout(700)

        if latest is not None:
            return latest
        raise TimeoutError("Timed out waiting for assistant response to complete.")

    async def _send_button_ready(self) -> bool:
        for selector in SEND_BUTTON_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return await locator.is_enabled()
            except Exception:
                continue
        return False

    async def _stop_button_visible(self) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Relay a prompt through the real ChatGPT web UI using a persistent "
            "browser profile instead of the blocked backend POST path."
        )
    )
    parser.add_argument("selector", help="Conversation UUID, full ChatGPT URL, or alias name.")
    parser.add_argument("--prompt", help="Prompt text to submit.")
    parser.add_argument("--prompt-file", type=Path, help="Read prompt text from a file.")
    parser.add_argument(
        "--alias-file",
        action="append",
        type=Path,
        default=[],
        help="Additional JSON alias file mapping short names to conversation ids/URLs.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="Persistent Chromium profile directory (default: %(default)s).",
    )
    parser.add_argument(
        "--print-latest",
        action="store_true",
        help="Do not send a prompt; just print the latest assistant turn from the target thread.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON payload instead of plain text.")
    parser.add_argument(
        "--transport",
        choices=("dom", "api"),
        default="dom",
        help="Submit through the composer DOM or browser-context API fetch (default: %(default)s).",
    )
    parser.add_argument("--headed", action="store_true", help="Launch a visible browser window.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Overall timeout in seconds for page load and response wait (default: %(default)s).",
    )
    parser.add_argument(
        "--idle-ms",
        type=int,
        default=2500,
        help="Assistant stability window before considering the reply complete (default: %(default)s).",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    alias_paths = [*DEFAULT_ALIAS_PATHS, *args.alias_file]
    alias_map = load_alias_map(alias_paths)
    thread_id = resolve_thread_selector(args.selector, alias_map)
    # Do not consume stdin for --print-latest. This matters when the command
    # is launched by a shell wrapper or test runner with piped stdin.
    stdin_text = None if args.print_latest or sys.stdin.isatty() else sys.stdin.read()
    prompt_text = resolve_prompt_text(
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        stdin_text=stdin_text,
        stdin_is_tty=sys.stdin.isatty(),
    )
    if args.print_latest and prompt_text is not None:
        raise ValueError("--print-latest cannot be combined with prompt input.")
    if not args.print_latest and prompt_text is None:
        raise ValueError("Provide --prompt, --prompt-file, or pipe prompt text on stdin.")

    async with ChatGPTDomRelay(
        profile_dir=args.profile_dir,
        headed=args.headed,
        timeout_ms=args.timeout * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        if args.transport == "api":
            await relay.open_api_context()
        else:
            await relay.open_thread(thread_id)
        if args.print_latest:
            if args.transport == "api":
                raise ValueError("--print-latest is only supported with --transport dom.")
            await relay.wait_for_history()
            result = summarize_snapshot(await relay.snapshot())
        elif args.transport == "api":
            result = await relay.send_api_prompt(prompt_text or "", thread_id)
        else:
            result = await relay.send_prompt(prompt_text or "")
        result["resolved_thread_id"] = thread_id
        return result


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
        text = str(result.get("latest_assistant") or "").strip()
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
