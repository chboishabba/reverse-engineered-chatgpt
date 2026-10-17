from __future__ import annotations

import asyncio

from re_gpt import browser_mutation
from re_gpt.browser_mutation import dismiss_floating_new_chat, focus_composer


class _Locator:
    def __init__(self, *, present: bool = False, child=None) -> None:
        self.present = present
        self.child = child
        self.clicked = 0

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return int(self.present)

    async def click(self) -> None:
        self.clicked += 1

    def locator(self, _selector):
        return self.child or _Locator()


class _Page:
    def __init__(self, dialog) -> None:
        self.dialog = dialog
        self.waits: list[int] = []

    def locator(self, _selector):
        return self.dialog

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def test_dismiss_floating_new_chat_minimizes_only_the_observed_dialog() -> None:
    minimize = _Locator(present=True)
    dialog = _Locator(present=True, child=minimize)
    page = _Page(dialog)

    dismissed = asyncio.run(dismiss_floating_new_chat(page))

    assert dismissed is True
    assert minimize.clicked == 1
    assert page.waits == [100]


def test_dismiss_floating_new_chat_leaves_absent_dialog_untouched() -> None:
    page = _Page(_Locator(present=False))

    assert asyncio.run(dismiss_floating_new_chat(page)) is False
    assert page.waits == []


def test_dismiss_floating_new_chat_handles_overlay_before_its_dialog_is_exposed() -> None:
    minimize = _Locator(present=True)
    overlay = _Locator(present=True)

    class DelayedDialogPage(_Page):
        def locator(self, selector):
            if "codex-dialog-overlay" in selector:
                return overlay
            if "Minimize chat" in selector:
                return minimize
            return _Locator(present=False)

    page = DelayedDialogPage(_Locator(present=False))

    assert asyncio.run(dismiss_floating_new_chat(page)) is True
    assert minimize.clicked == 1


def test_focus_composer_retries_once_after_known_overlay_is_dismissed(monkeypatch) -> None:
    class Composer:
        def __init__(self):
            self.clicks = 0

        async def click(self, **_kwargs):
            self.clicks += 1
            if self.clicks == 1:
                raise RuntimeError("overlay intercepted focus")

    composer = Composer()
    relay = type("Relay", (), {"page": object(), "timeout_ms": 60_000})()
    calls = []

    async def dismiss(_page, *, settle_ms=0):
        calls.append(settle_ms)
        return bool(calls and len(calls) == 2)

    monkeypatch.setattr(browser_mutation, "dismiss_floating_new_chat", dismiss)

    asyncio.run(focus_composer(relay, composer))

    assert composer.clicks == 2
    assert calls == [750, 750]
