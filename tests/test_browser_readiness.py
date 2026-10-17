import asyncio
from types import SimpleNamespace

import re_gpt.browser_readiness as readiness
from chatgpt_dom_relay import SEND_BUTTON_SELECTORS, TURN_SELECTOR, build_conversation_url
from re_gpt.browser_readiness import PENDING_EDITOR_SELECTOR, REAL_EDITOR_SELECTORS, RENDERED_TURN_SELECTORS


def test_production_readiness_uses_real_prosemirror_editor():
    assert PENDING_EDITOR_SELECTOR == "#pending-conversation-input"
    assert REAL_EDITOR_SELECTORS
    assert all("ProseMirror" in selector for selector in REAL_EDITOR_SELECTORS)
    assert all("pending-conversation-input" not in selector for selector in REAL_EDITOR_SELECTORS)
    assert all(not selector.lstrip().startswith("textarea") for selector in REAL_EDITOR_SELECTORS)


def test_readiness_requires_a_visible_turn_not_just_a_thread_title(monkeypatch):
    """A title may arrive before the conversation history is hydrated."""

    async def authenticated(_page):
        return True

    async def visible(_page, selector):
        if selector == TURN_SELECTOR:
            return 0
        if selector == PENDING_EDITOR_SELECTOR:
            return 0
        if selector in REAL_EDITOR_SELECTORS or selector in SEND_BUTTON_SELECTORS:
            return 1
        return 0

    monkeypatch.setattr(readiness, "authenticated_session", authenticated)
    monkeypatch.setattr(readiness, "_visible_count", visible)
    class Locator:
        first = property(lambda self: self)

        async def count(self):
            return 1

        async def get_attribute(self, _name):
            return None

    page = SimpleNamespace(
        url=build_conversation_url("00000000-0000-0000-0000-000000000001"),
        title=lambda: None,
        locator=lambda _selector: Locator(),
    )

    async def title():
        return "Already titled thread - ChatGPT"

    page.title = title
    result = asyncio.run(readiness.inspect_readiness(SimpleNamespace(page=page), "00000000-0000-0000-0000-000000000001"))

    assert result.history_hydrated is False
    assert result.ready is False


def test_readiness_accepts_current_rendered_turns_without_a_pre_insert_send_button(monkeypatch):
    """The empty current composer exposes Start Voice; Send appears after text."""

    async def authenticated(_page):
        return True

    async def visible(_page, selector):
        if selector == PENDING_EDITOR_SELECTOR or selector in SEND_BUTTON_SELECTORS:
            return 0
        if selector in REAL_EDITOR_SELECTORS or selector == 'button[aria-label="Copy message"]':
            return 1
        return 0

    monkeypatch.setattr(readiness, "authenticated_session", authenticated)
    monkeypatch.setattr(readiness, "_visible_count", visible)

    class Locator:
        first = property(lambda self: self)

        async def count(self):
            return 0

    page = SimpleNamespace(
        url=build_conversation_url("00000000-0000-0000-0000-000000000001"),
        locator=lambda _selector: Locator(),
    )
    result = asyncio.run(readiness.inspect_readiness(SimpleNamespace(page=page), "00000000-0000-0000-0000-000000000001"))

    assert 'button[aria-label="Copy message"]' in RENDERED_TURN_SELECTORS
    assert result.history_hydrated is True
    assert result.send_control_present is False
    assert result.ready is True
