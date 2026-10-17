"""Strict readiness checks for browser-owned ChatGPT mutations.

A visible textarea is not sufficient evidence that an existing conversation is
ready.  ChatGPT can render ``#pending-conversation-input`` before the target
thread and real composer hydrate.  Production mutation code uses this module to
fail closed until the authenticated target thread is ready.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from chatgpt_dom_relay import EDITOR_TEXT_SELECTORS, SEND_BUTTON_SELECTORS, TURN_SELECTOR, build_conversation_url


@dataclass(frozen=True)
class BrowserReadiness:
    conversation_id: str
    authenticated: bool
    history_hydrated: bool
    pending_composer_absent: bool
    editor_present: bool
    send_control_present: bool
    send_control_busy: bool

    @property
    def ready(self) -> bool:
        return (
            self.authenticated
            and self.history_hydrated
            and self.pending_composer_absent
            and self.editor_present
            and self.send_control_present
            and not self.send_control_busy
        )


async def authenticated_session(page) -> bool:
    """Return True only when ChatGPT exposes an authenticated access token."""

    result = await page.evaluate(
        """async () => {
          try {
            const response = await fetch('/api/auth/session', {credentials: 'include'});
            if (!response.ok) return {ok: false, status: response.status};
            const body = await response.json();
            return {ok: Boolean(body && body.accessToken), status: response.status};
          } catch (error) {
            return {ok: false, status: 0, error: String(error)};
          }
        }"""
    )
    return bool(isinstance(result, dict) and result.get("ok"))


async def _visible_count(page, selector: str) -> int:
    try:
        return int(await page.locator(f"{selector}:visible").count())
    except Exception:
        return 0


async def inspect_readiness(relay, thread_id: str) -> BrowserReadiness:
    expected_url = build_conversation_url(thread_id)
    current_url = str(relay.page.url or "").split("?", 1)[0].rstrip("/")
    if current_url != expected_url.rstrip("/"):
        return BrowserReadiness(thread_id, False, False, False, False, False, False)

    authenticated = await authenticated_session(relay.page)
    pending_absent = await _visible_count(relay.page, "#pending-conversation-input") == 0

    editor_present = False
    for selector in EDITOR_TEXT_SELECTORS:
        if "pending-conversation-input" in selector:
            continue
        if await _visible_count(relay.page, selector):
            editor_present = True
            break

    send_present = False
    send_busy = False
    for selector in SEND_BUTTON_SELECTORS:
        locator = relay.page.locator(f"{selector}:visible").first
        try:
            if not await locator.count():
                continue
            send_present = True
            aria_busy = str(await locator.get_attribute("aria-busy") or "").lower()
            send_busy = aria_busy == "true"
            break
        except Exception:
            continue

    history_hydrated = await _visible_count(relay.page, TURN_SELECTOR) > 0
    if not history_hydrated:
        # Some very short/legacy threads momentarily have no labelled turns.
        # A non-generic hydrated title is acceptable metadata evidence, but the
        # pending composer must still be gone and the real editor present.
        try:
            title = str(await relay.page.title() or "").strip().lower()
        except Exception:
            title = ""
        history_hydrated = bool(title and title not in {"chatgpt", "new chat"})

    return BrowserReadiness(
        conversation_id=thread_id,
        authenticated=authenticated,
        history_hydrated=history_hydrated,
        pending_composer_absent=pending_absent,
        editor_present=editor_present,
        send_control_present=send_present,
        send_control_busy=send_busy,
    )


async def open_ready_thread(relay, thread_id: str) -> BrowserReadiness:
    """Navigate to an existing thread and wait for authenticated real-composer readiness."""

    url = build_conversation_url(thread_id)
    relay._log(f"opening {url}")
    await relay.page.goto(url, wait_until="commit", timeout=relay.timeout_ms)

    deadline = time.monotonic() + relay.timeout_ms / 1000.0
    last: BrowserReadiness | None = None
    while time.monotonic() < deadline:
        current_url = str(relay.page.url or "")
        if "/auth/" in current_url or "/login" in current_url:
            raise RuntimeError("browser profile is not authenticated")
        title = str(await relay.page.title() or "")
        if "just a moment" in title.lower():
            raise RuntimeError("Cloudflare challenge is active; use an authenticated supported browser profile")

        last = await inspect_readiness(relay, thread_id)
        if not last.authenticated:
            # Authentication is a hard precondition, not a readiness condition.
            raise RuntimeError("browser profile is not authenticated")
        if last.ready:
            relay._log("authenticated target thread and real composer are ready")
            return last
        await relay.page.wait_for_timeout(200)

    raise TimeoutError(f"timed out waiting for hydrated thread readiness: {last!r}")
