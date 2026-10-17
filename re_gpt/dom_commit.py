"""Exactly-once receipts for browser DOM submission.

The legacy DOM relay used to click the send button and immediately wait for an
assistant reply.  A no-op click and a committed-but-slow mutation therefore
collapsed into the same timeout.  This compatibility layer observes mutation
signals before waiting for the assistant and only retries the trigger with
Enter when there is positive evidence that the click did not start submission.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import asdict, dataclass
from typing import Any

from chatgpt_dom_relay import (
    EDITOR_TEXT_SELECTORS,
    ChatGPTDomRelay,
    assistant_reply_after_rendered_text,
    summarize_snapshot,
)


@dataclass(frozen=True)
class DomSubmissionReceipt:
    """Signals observed after a DOM submission trigger.

    ``commit_evidence`` means that a second trigger is unsafe, not that the
    backend graph has already been proven durable.  Canonical graph
    confirmation is performed separately through the validated sync reader.
    """

    mutation_request_seen: bool
    prompt_rendered: bool
    stop_visible: bool
    composer_cleared: bool
    composer_text: str

    @property
    def commit_evidence(self) -> bool:
        return any(
            (
                self.mutation_request_seen,
                self.prompt_rendered,
                self.stop_visible,
                self.composer_cleared,
            )
        )


class DomSubmissionNotObserved(RuntimeError):
    """Raised when neither click nor Enter produces any submission signal."""


def is_conversation_mutation_request(method: str, url: str) -> bool:
    """Return whether a browser request is the conversation mutation POST."""

    if (method or "").upper() != "POST":
        return False
    path = (url or "").split("?", 1)[0].rstrip("/")
    return path.endswith("/backend-api/conversation")


def rendered_prompt_count(page_text: str, prompt: str) -> int:
    """Count rendered user-turn markers for an exact prompt."""

    marker = f"You said:\n{prompt.strip()}"
    return (page_text or "").count(marker)


def should_retry_dom_with_enter(receipt: DomSubmissionReceipt, prompt: str) -> bool:
    """Allow a second trigger only for a positively observed no-op click."""

    if receipt.commit_evidence:
        return False
    return receipt.composer_text.strip() == prompt.strip()


async def _composer_text(composer) -> str:
    try:
        tag = str(await composer.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag = ""
    if tag in {"input", "textarea"}:
        with contextlib.suppress(Exception):
            return str(await composer.input_value()).strip()
    with contextlib.suppress(Exception):
        return str(await composer.inner_text()).strip()
    with contextlib.suppress(Exception):
        return str(await composer.text_content() or "").strip()
    return ""


async def _main_text(relay: ChatGPTDomRelay) -> str:
    with contextlib.suppress(Exception):
        return str(await relay.page.locator("main").inner_text())
    return ""


async def _observe_submission_receipt(
    relay: ChatGPTDomRelay,
    *,
    prompt: str,
    composer,
    baseline_prompt_count: int,
    mutation_requests: list[str],
    window_ms: int = 5000,
) -> DomSubmissionReceipt:
    deadline = time.monotonic() + max(0.25, window_ms / 1000.0)
    latest = DomSubmissionReceipt(False, False, False, False, prompt)

    while time.monotonic() < deadline:
        composer_text = await _composer_text(composer)
        page_text = await _main_text(relay)
        latest = DomSubmissionReceipt(
            mutation_request_seen=bool(mutation_requests),
            prompt_rendered=rendered_prompt_count(page_text, prompt) > baseline_prompt_count,
            stop_visible=await relay._stop_button_visible(),
            composer_cleared=not composer_text,
            composer_text=composer_text,
        )
        if latest.commit_evidence:
            return latest
        await relay.page.wait_for_timeout(100)

    return latest


async def _wait_for_new_assistant_turn(
    relay: ChatGPTDomRelay,
    *,
    prompt: str,
    baseline_prompt_count: int,
) -> dict[str, Any]:
    """Wait for an assistant reply belonging to a newly rendered prompt turn."""

    deadline = time.monotonic() + (relay.timeout_ms / 1000.0)
    latest: dict[str, Any] | None = None
    latest_signature = ""
    stable_since: float | None = None

    while time.monotonic() < deadline:
        page_text = await _main_text(relay)
        if rendered_prompt_count(page_text, prompt) > baseline_prompt_count:
            reply = assistant_reply_after_rendered_text(page_text, prompt)
            if reply is not None:
                snapshot = summarize_snapshot(await relay.snapshot())
                latest = snapshot
                latest["latest_assistant"] = reply
                signature = reply[-400:]
                if signature != latest_signature:
                    latest_signature = signature
                    stable_since = time.monotonic()
                elif (
                    stable_since is not None
                    and (time.monotonic() - stable_since) * 1000 >= relay.idle_ms
                    and (await relay._send_button_ready() or not await relay._stop_button_visible())
                ):
                    return latest
        await relay.page.wait_for_timeout(700)

    if latest is not None:
        return latest
    raise TimeoutError("Timed out waiting for assistant response after observed DOM submission.")


async def send_prompt_with_receipt(self: ChatGPTDomRelay, prompt: str) -> dict[str, Any]:
    """Submit through the DOM with a trigger receipt and exactly-once fallback."""

    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is empty")

    self._log("typing prompt")
    composer = await self._locate_visible(EDITOR_TEXT_SELECTORS)
    self._log("focusing composer")
    await composer.click()
    self._log("inserting prompt")
    await self.page.keyboard.insert_text(prompt)

    baseline_prompt_count = rendered_prompt_count(await _main_text(self), prompt)
    mutation_requests: list[str] = []

    def on_request(request) -> None:
        try:
            if is_conversation_mutation_request(request.method, request.url):
                mutation_requests.append(request.url)
        except Exception:
            return

    self.page.on("request", on_request)
    trigger = "click"
    try:
        self._log("sending prompt via click")
        send_button = await self._wait_for_enabled_send_button()
        await send_button.click()
        receipt = await _observe_submission_receipt(
            self,
            prompt=prompt,
            composer=composer,
            baseline_prompt_count=baseline_prompt_count,
            mutation_requests=mutation_requests,
        )

        if should_retry_dom_with_enter(receipt, prompt):
            trigger = "enter"
            self._log("click produced no submission signal; retrying trigger with Enter")
            await composer.press("Enter")
            receipt = await _observe_submission_receipt(
                self,
                prompt=prompt,
                composer=composer,
                baseline_prompt_count=baseline_prompt_count,
                mutation_requests=mutation_requests,
            )

        if not receipt.commit_evidence:
            raise DomSubmissionNotObserved(
                "DOM send produced no mutation request, rendered user turn, stop state, "
                "or composer clear after click and Enter; canonical graph should remain unchanged."
            )
    finally:
        with contextlib.suppress(Exception):
            self.page.remove_listener("request", on_request)

    self._log(f"submission signal observed via {trigger}; waiting for assistant response")
    try:
        result = await _wait_for_new_assistant_turn(
            self,
            prompt=prompt,
            baseline_prompt_count=baseline_prompt_count,
        )
    except TimeoutError:
        snapshot = summarize_snapshot(await self.snapshot())
        return {
            **snapshot,
            "latest_assistant": "",
            "mutation_state": "submitted_unconfirmed",
            "submission_trigger": trigger,
            "submission_receipt": asdict(receipt),
        }

    result["mutation_state"] = "assistant_observed"
    result["submission_trigger"] = trigger
    result["submission_receipt"] = asdict(receipt)
    return result


def install_dom_commit_receipts() -> None:
    """Install the receipt-aware DOM sender once for all relay entrypoints."""

    if getattr(ChatGPTDomRelay.send_prompt, "_re_gpt_receipt_aware", False):
        return
    send_prompt_with_receipt._re_gpt_receipt_aware = True  # type: ignore[attr-defined]
    ChatGPTDomRelay.send_prompt = send_prompt_with_receipt  # type: ignore[method-assign]
