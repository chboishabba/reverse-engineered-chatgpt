from __future__ import annotations

from chatgpt_dom_relay import BrowserAPIResponseError
from re_gpt.accessibility_browser import api_failure_allows_dom_fallback


def test_explicit_browser_api_rejection_allows_dom_fallback() -> None:
    error = BrowserAPIResponseError(
        stage="sentinel",
        status=429,
        body="Too many requests",
    )
    assert api_failure_allows_dom_fallback(error) is True


def test_ambiguous_browser_exception_never_allows_second_submission() -> None:
    # A page/CDP/evaluation failure can occur after the conversation POST was
    # accepted, so automatically sending the same prompt through the DOM could
    # duplicate it.
    assert api_failure_allows_dom_fallback(TimeoutError("CDP response lost")) is False
    assert api_failure_allows_dom_fallback(RuntimeError("page closed")) is False
