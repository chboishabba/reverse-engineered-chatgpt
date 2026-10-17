"""Strict readiness checks for browser-owned ChatGPT mutations."""

from __future__ import annotations

import time
from dataclasses import dataclass

from chatgpt_dom_relay import SEND_BUTTON_SELECTORS, TURN_SELECTOR, build_conversation_url

# The live UI exposes the actual editor as ProseMirror.  Deliberately do not
# include textarea fallbacks here: #pending-conversation-input is a loading-state
# control and must never qualify an existing thread as mutation-ready.
REAL_EDITOR_SELECTORS = (
    '#prompt-textarea.ProseMirror[contenteditable="true"]',
    '[data-testid="composer-root"] .ProseMirror[contenteditable="true"]',
    '.ProseMirror[contenteditable="true"][role="textbox"]',
)
PENDING_EDITOR_SELECTOR = '#pending-conversation-input'
RENDERED_TURN_SELECTORS = (
    TURN_SELECTOR,
    'button[aria-label="Copy message"]',
    'button[aria-label="Edit message"]',
)


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
        return all((
            self.authenticated,
            self.history_hydrated,
            self.pending_composer_absent,
            self.editor_present,
        ))


async def authenticated_session(page) -> bool:
    result = await page.evaluate("""async () => {
      try {
        const response = await fetch('/api/auth/session', {credentials: 'include'});
        if (!response.ok) return {ok:false, status:response.status};
        const body = await response.json();
        return {ok:Boolean(body && body.accessToken), status:response.status};
      } catch (error) { return {ok:false, status:0, error:String(error)}; }
    }""")
    return bool(isinstance(result, dict) and result.get("ok"))


async def _visible_count(page, selector: str) -> int:
    try:
        return int(await page.locator(f"{selector}:visible").count())
    except Exception:
        return 0


async def inspect_readiness(relay, thread_id: str) -> BrowserReadiness:
    expected_url = build_conversation_url(thread_id).rstrip("/")
    current_url = str(relay.page.url or "").split("?", 1)[0].rstrip("/")
    if current_url != expected_url:
        return BrowserReadiness(thread_id, False, False, False, False, False, False)

    authenticated = await authenticated_session(relay.page)
    pending_absent = await _visible_count(relay.page, PENDING_EDITOR_SELECTOR) == 0
    editor_present = any([await _visible_count(relay.page, selector) > 0 for selector in REAL_EDITOR_SELECTORS])

    send_present = False
    send_busy = False
    for selector in SEND_BUTTON_SELECTORS:
        locator = relay.page.locator(f"{selector}:visible").first
        try:
            if not await locator.count():
                continue
            send_present = True
            send_busy = str(await locator.get_attribute("aria-busy") or "").lower() == "true"
            break
        except Exception:
            continue

    # A title is populated before virtualized conversation content; it cannot
    # certify that this exact thread's history has hydrated.
    history_hydrated = any([
        await _visible_count(relay.page, selector) > 0
        for selector in RENDERED_TURN_SELECTORS
    ])

    return BrowserReadiness(
        thread_id, authenticated, history_hydrated, pending_absent,
        editor_present, send_present, send_busy,
    )


async def open_ready_thread(relay, thread_id: str) -> BrowserReadiness:
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
            raise RuntimeError("browser profile is not authenticated")
        if last.ready:
            relay._log("authenticated hydrated thread and real ProseMirror composer are ready")
            return last
        await relay.page.wait_for_timeout(200)
    raise TimeoutError(f"timed out waiting for hydrated ProseMirror readiness: {last!r}")
