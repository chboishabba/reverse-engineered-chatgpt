from __future__ import annotations

import pytest

from re_gpt.graph_broker import (
    GraphAcquisitionFailed,
    RequestContextRejected,
    fetch_exact_graph,
)


def _graph(label: str) -> dict:
    return {"title": label, "mapping": {"node": {"message": None}}}


def test_async_is_first_and_stops_after_valid_graph() -> None:
    calls: list[str] = []
    def async_ok(_conversation_id: str):
        calls.append("async")
        return _graph("async"), {"status": 200}
    def sync_should_not_run(_conversation_id: str):
        calls.append("sync")
        raise AssertionError("sync should not run")
    result = fetch_exact_graph("abc", adapter_fetchers={"async": async_ok, "sync": sync_should_not_run}, adapter_order=("async", "sync"))
    assert result.adapter == "async"
    assert result.graph["title"] == "async"
    assert result.attempted_adapters == ("async",)
    assert result.rejected_adapters == ()
    assert calls == ["async"]


def test_async_429_is_quarantined_and_sync_can_succeed() -> None:
    calls: list[str] = []
    def async_rejected(_conversation_id: str):
        calls.append("async")
        raise RequestContextRejected("async", status=429)
    def sync_ok(_conversation_id: str):
        calls.append("sync")
        return _graph("sync"), {"status": 200}
    result = fetch_exact_graph("abc", adapter_fetchers={"async": async_rejected, "sync": sync_ok}, adapter_order=("async", "sync"))
    assert result.adapter == "sync"
    assert result.attempted_adapters == ("async", "sync")
    assert result.rejected_adapters == ("async",)
    assert calls == ["async", "sync"]


def test_browser_is_third_after_async_and_sync_rejection() -> None:
    calls: list[str] = []
    def rejected(adapter: str):
        def _fetch(_conversation_id: str):
            calls.append(adapter)
            raise RuntimeError("HTTP 429 Too Many Requests")
        return _fetch
    def browser_ok(_conversation_id: str):
        calls.append("browser")
        return _graph("browser"), {"status": 200}
    result = fetch_exact_graph("abc", adapter_fetchers={"async": rejected("async"), "sync": rejected("sync"), "browser": browser_ok})
    assert result.adapter == "browser"
    assert result.rejected_adapters == ("async", "sync")
    assert result.attempted_adapters == ("async", "sync", "browser")
    assert calls == ["async", "sync", "browser"]


def test_unlabeled_429_like_number_is_not_treated_as_http_rejection() -> None:
    def async_broken(_conversation_id: str):
        raise RuntimeError("operation 429 milliseconds after start")
    def sync_ok(_conversation_id: str):
        return _graph("sync"), {"status": 200}
    result = fetch_exact_graph("abc", adapter_fetchers={"async": async_broken, "sync": sync_ok}, adapter_order=("async", "sync"))
    assert result.adapter == "sync"
    assert result.attempts[0]["status"] is None
    assert result.rejected_adapters == ()


def test_invalid_graph_falls_through_without_marking_bot_rejection() -> None:
    def async_invalid(_conversation_id: str):
        return {"mapping": {}}, {"status": 200}
    def sync_ok(_conversation_id: str):
        return _graph("sync"), {"status": 200}
    result = fetch_exact_graph("abc", adapter_fetchers={"async": async_invalid, "sync": sync_ok}, adapter_order=("async", "sync"))
    assert result.adapter == "sync"
    assert result.rejected_adapters == ()
    assert result.attempts[0]["response_class"] == "ValueError"


def test_each_adapter_is_attempted_at_most_once() -> None:
    counts = {"async": 0, "sync": 0, "browser": 0}
    def rejected(adapter: str):
        def _fetch(_conversation_id: str):
            counts[adapter] += 1
            raise RequestContextRejected(adapter, status=429)
        return _fetch
    with pytest.raises(GraphAcquisitionFailed) as excinfo:
        fetch_exact_graph("abc", adapter_fetchers={name: rejected(name) for name in counts}, adapter_order=("async", "sync", "browser"))
    assert counts == {"async": 1, "sync": 1, "browser": 1}
    assert excinfo.value.rejected_adapters == ("async", "sync", "browser")
    assert excinfo.value.attempted_adapters == ("async", "sync", "browser")


def test_generic_adapter_failure_does_not_abort_remaining_adapters() -> None:
    def async_broken(_conversation_id: str):
        raise OSError("transport reset")
    def sync_ok(_conversation_id: str):
        return _graph("sync"), {"status": 200}
    result = fetch_exact_graph("abc", adapter_fetchers={"async": async_broken, "sync": sync_ok}, adapter_order=("async", "sync"))
    assert result.adapter == "sync"
    assert result.attempts[0]["response_class"] == "OSError"
    assert result.rejected_adapters == ()


def test_all_failures_are_exposed_without_secret_error_bodies() -> None:
    def async_rejected(_conversation_id: str):
        raise RuntimeError("HTTP 429 Too Many Requests SECRET-BODY")
    with pytest.raises(GraphAcquisitionFailed) as excinfo:
        fetch_exact_graph("abc", adapter_fetchers={"async": async_rejected}, adapter_order=("async",))
    failure = excinfo.value
    assert failure.rejected_adapters == ("async",)
    assert failure.attempts[0]["status"] == 429
    assert "SECRET-BODY" not in repr(failure.attempts)
