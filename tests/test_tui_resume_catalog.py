"""Offline regression tests for TUI catalog diagnostics."""
import pytest

from re_gpt.upstream_tui_adapter import collect_remote_catalog


class Catalog:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_conversations_page(self, *, offset, limit):
        self.calls.append((offset, limit))
        return self.pages.get(offset, {"items": []})


def test_catalog_paginates_without_duplicates():
    first = [{"id": str(i), "title": "Conversation " + str(i)} for i in range(100)]
    provider = Catalog({0: {"items": first}, 100: {"items": [first[0], {"id": "100", "title": "Last"}]}})
    rows = collect_remote_catalog(provider)
    assert len(rows) == 101
    assert provider.calls == [(0, 100), (100, 100)]


def test_empty_remote_catalog_is_diagnostic_not_search_miss():
    with pytest.raises(RuntimeError, match="zero conversations"):
        collect_remote_catalog(Catalog({0: {"items": []}}))


def test_malformed_response_fails_closed():
    with pytest.raises(RuntimeError, match="unexpected response shape"):
        collect_remote_catalog(Catalog({0: {"objects": []}}))


def test_duplicate_page_loop_terminates():
    first = [{"id": str(i)} for i in range(100)]
    rows = collect_remote_catalog(Catalog({0: {"items": first}, 100: {"items": first}}))
    assert len(rows) == 100
