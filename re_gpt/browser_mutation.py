"""Production DOM mutation primitives.

Submission and assistant following are deliberately separate.  A successful
send is certified later by the canonical conversation graph; browser evidence
only makes a second trigger unsafe.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import asdict
from typing import Any

from chatgpt_dom_relay import EDITOR_TEXT_SELECTORS
from .dom_commit import (
    DomSubmissionNotObserved,
    _main_text,
    _observe_submission_receipt,
    _wait_for_new_assistant_turn,
    rendered_prompt_count,
    should_retry_dom_with_enter,
)

FLOATING_NEW_CHAT_DIALOG = 'section[role="dialog"][aria-label="New chat"]'
FLOATING_NEW_CHAT_OVERLAY = '.codex-dialog-overlay[data-state="open"]'
FLOATING_NEW_CHAT_MINIMIZE = 'button[aria-label="Minimize chat"]'


async def dismiss_floating_new_chat(page, *, settle_ms: int = 0) -> bool:
    """Minimize ChatGPT's known floating New chat overlay if it appears."""

    deadline = time.monotonic() + max(0, settle_ms) / 1000.0
    while True:
        dialog = page.locator(f"{FLOATING_NEW_CHAT_DIALOG}:visible").first
        overlay = page.locator(f"{FLOATING_NEW_CHAT_OVERLAY}:visible").first
        if await dialog.count():
            minimize = dialog.locator(f"{FLOATING_NEW_CHAT_MINIMIZE}:visible").first
        elif await overlay.count():
            minimize = page.locator(f"{FLOATING_NEW_CHAT_MINIMIZE}:visible").first
        else:
            minimize = None
        if minimize is not None and await minimize.count():
            await minimize.click()
            await page.wait_for_timeout(100)
            return True
        if time.monotonic() >= deadline:
            return False
        await page.wait_for_timeout(100)


async def focus_composer(relay, composer) -> None:
    """Focus the composer, retrying once only after the known overlay appears."""

    await dismiss_floating_new_chat(relay.page, settle_ms=750)
    try:
        await composer.click(timeout=min(2_000, relay.timeout_ms))
    except Exception:
        if not await dismiss_floating_new_chat(relay.page, settle_ms=750):
            raise
        await composer.click()


async def submit_prompt_once(relay, prompt: str) -> dict[str, Any]:
    """Insert and trigger a prompt once, returning after browser submission evidence."""

    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is empty")

    composer = await relay._locate_visible(EDITOR_TEXT_SELECTORS)
    await focus_composer(relay, composer)
    await relay.page.keyboard.insert_text(prompt)

    # Readiness is rechecked after insertion by requiring a usable send control.
    send_button = await relay._wait_for_enabled_send_button()
    baseline_prompt_count = rendered_prompt_count(await _main_text(relay), prompt)
    mutation_requests: list[str] = []

    def on_request(request) -> None:
        try:
            method = str(request.method or "").upper()
            path = str(request.url or "").split("?", 1)[0].rstrip("/")
            if method == "POST" and (
                path.endswith("/backend-api/conversation")
                or "/backend-api/conversation/" in path
                or path.endswith("/conversation/init")
            ):
                mutation_requests.append(str(request.url))
        except Exception:
            return

    relay.page.on("request", on_request)
    trigger = "click"
    try:
        await send_button.click()
        receipt = await _observe_submission_receipt(
            relay,
            prompt=prompt,
            composer=composer,
            baseline_prompt_count=baseline_prompt_count,
            mutation_requests=mutation_requests,
        )
        if should_retry_dom_with_enter(receipt, prompt):
            trigger = "enter"
            await composer.press("Enter")
            receipt = await _observe_submission_receipt(
                relay,
                prompt=prompt,
                composer=composer,
                baseline_prompt_count=baseline_prompt_count,
                mutation_requests=mutation_requests,
            )
        if not receipt.commit_evidence:
            raise DomSubmissionNotObserved(
                "DOM send produced no mutation request, rendered user turn, stop state, "
                "or composer clear; refusing any further trigger"
            )
    finally:
        with contextlib.suppress(Exception):
            relay.page.remove_listener("request", on_request)

    snapshot = await relay.snapshot()
    return {
        "conversation_id": snapshot.get("conversation_id"),
        "url": snapshot.get("url"),
        "mutation_state": "submission_observed",
        "submission_trigger": trigger,
        "submission_receipt": asdict(receipt),
        "baseline_prompt_count": baseline_prompt_count,
        "latest_assistant": "",
    }


async def follow_prompt(relay, prompt: str, baseline_prompt_count: int) -> dict[str, Any]:
    """Wait for a settled assistant turn after an already-triggered prompt."""

    return await _wait_for_new_assistant_turn(
        relay,
        prompt=prompt.strip(),
        baseline_prompt_count=int(baseline_prompt_count),
    )
