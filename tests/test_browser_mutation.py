from __future__ import annotations

import asyncio

from re_gpt.browser_mutation import dismiss_floating_new_chat


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
