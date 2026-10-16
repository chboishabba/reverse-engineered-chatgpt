from __future__ import annotations

import asyncio
from types import SimpleNamespace

from re_gpt import dom_commit


class FakeKeyboard:
    def __init__(self) -> None:
        self.inserted: list[str] = []

    async def insert_text(self, text: str) -> None:
        self.inserted.append(text)


class FakePage:
    def __init__(self) -> None:
        self.keyboard = FakeKeyboard()
        self.listeners: dict[str, object] = {}

    def on(self, name: str, callback) -> None:
        self.listeners[name] = callback

    def remove_listener(self, name: str, callback) -> None:
        if self.listeners.get(name) is callback:
            self.listeners.pop(name, None)


class FakeComposer:
    def __init__(self) -> None:
        self.clicked = 0
        self.pressed: list[str] = []

    async def click(self) -> None:
        self.clicked += 1

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakeSendButton:
    def __init__(self) -> None:
        self.clicked = 0

    async def click(self) -> None:
        self.clicked += 1


class FakeRelay:
    timeout_ms = 1000
    idle_ms = 1

    def __init__(self) -> None:
        self.page = FakePage()
        self.composer = FakeComposer()
        self.send_button = FakeSendButton()
        self.logs: list[str] = []

    def _log(self, message: str) -> None:
        self.logs.append(message)

    async def _locate_visible(self, selectors):
        return self.composer

    async def _wait_for_enabled_send_button(self):
        return self.send_button

    async def snapshot(self):
        return {"conversation_id": "abc", "url": "https://chatgpt.com/c/abc", "title": "Demo", "turns": []}


def test_noop_click_retries_trigger_with_enter(monkeypatch) -> None:
    relay = FakeRelay()
    receipts = iter(
        [
            dom_commit.DomSubmissionReceipt(False, False, False, False, "hello"),
            dom_commit.DomSubmissionReceipt(True, False, False, True, ""),
        ]
    )

    async def fake_main_text(relay):
        return ""

    async def fake_observe(*args, **kwargs):
        return next(receipts)

    async def fake_wait(*args, **kwargs):
        return {"latest_assistant": "ACK"}

    monkeypatch.setattr(dom_commit, "_main_text", fake_main_text)
    monkeypatch.setattr(dom_commit, "_observe_submission_receipt", fake_observe)
    monkeypatch.setattr(dom_commit, "_wait_for_new_assistant_turn", fake_wait)

    result = asyncio.run(dom_commit.send_prompt_with_receipt(relay, "hello"))

    assert relay.send_button.clicked == 1
    assert relay.composer.pressed == ["Enter"]
    assert result["submission_trigger"] == "enter"
    assert result["mutation_state"] == "assistant_observed"


def test_observed_click_does_not_send_enter(monkeypatch) -> None:
    relay = FakeRelay()
    receipt = dom_commit.DomSubmissionReceipt(True, False, False, True, "")

    async def fake_main_text(relay):
        return ""

    async def fake_observe(*args, **kwargs):
        return receipt

    async def fake_wait(*args, **kwargs):
        return {"latest_assistant": "ACK"}

    monkeypatch.setattr(dom_commit, "_main_text", fake_main_text)
    monkeypatch.setattr(dom_commit, "_observe_submission_receipt", fake_observe)
    monkeypatch.setattr(dom_commit, "_wait_for_new_assistant_turn", fake_wait)

    result = asyncio.run(dom_commit.send_prompt_with_receipt(relay, "hello"))

    assert relay.composer.pressed == []
    assert result["submission_trigger"] == "click"


def test_observed_submission_timeout_returns_unconfirmed_receipt(monkeypatch) -> None:
    relay = FakeRelay()
    receipt = dom_commit.DomSubmissionReceipt(True, False, False, True, "")

    async def fake_main_text(relay):
        return ""

    async def fake_observe(*args, **kwargs):
        return receipt

    async def fake_wait(*args, **kwargs):
        raise TimeoutError("no assistant")

    monkeypatch.setattr(dom_commit, "_main_text", fake_main_text)
    monkeypatch.setattr(dom_commit, "_observe_submission_receipt", fake_observe)
    monkeypatch.setattr(dom_commit, "_wait_for_new_assistant_turn", fake_wait)

    result = asyncio.run(dom_commit.send_prompt_with_receipt(relay, "hello"))

    assert result["mutation_state"] == "submitted_unconfirmed"
    assert result["latest_assistant"] == ""
    assert result["submission_receipt"]["mutation_request_seen"] is True
