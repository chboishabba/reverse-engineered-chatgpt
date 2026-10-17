from re_gpt.browser_readiness import PENDING_EDITOR_SELECTOR, REAL_EDITOR_SELECTORS


def test_production_readiness_uses_real_prosemirror_editor():
    assert PENDING_EDITOR_SELECTOR == "#pending-conversation-input"
    assert REAL_EDITOR_SELECTORS
    assert all("ProseMirror" in selector for selector in REAL_EDITOR_SELECTORS)
    assert all("pending-conversation-input" not in selector for selector in REAL_EDITOR_SELECTORS)
    assert all("textarea" not in selector for selector in REAL_EDITOR_SELECTORS)
