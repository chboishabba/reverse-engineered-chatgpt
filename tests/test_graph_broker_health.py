from __future__ import annotations

from re_gpt.adapter_health import AdapterHealthStore
from re_gpt.graph_broker import fetch_exact_graph


def _graph():
    return {"mapping": {"root": {"message": None, "parent": None, "children": []}}}


def test_broker_records_failure_then_success_and_uses_next_adapter(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    calls: list[str] = []

    def bad(_conversation_id: str):
        calls.append("async")
        raise RuntimeError("HTTP 429 Too Many Requests")

    def good(_conversation_id: str):
        calls.append("sync")
        return _graph(), {"status": 200, "response_class": "ok"}

    result = fetch_exact_graph(
        "conversation-1",
        adapter_fetchers={"async": bad, "sync": good},
        adapter_order=("async", "sync"),
        health_db_path=db,
    )
    assert result.adapter == "sync"
    assert calls == ["async", "sync"]
    with AdapterHealthStore(db) as store:
        snap = store.snapshot()
    assert snap["async"]["rejection_count"] == 1
    assert snap["sync"]["success_count"] == 1


def test_broker_skips_adapter_still_in_cooldown(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    with AdapterHealthStore(db, now=lambda: 100.0) as store:
        store.record_attempt("async", elapsed_ms=5, status=429, response_class="RequestContextRejected", rejected=True, success=False)

    calls: list[str] = []
    result = fetch_exact_graph(
        "conversation-1",
        adapter_fetchers={
            "async": lambda _: calls.append("async") or _graph(),
            "sync": lambda _: calls.append("sync") or _graph(),
        },
        adapter_order=("async", "sync"),
        health_db_path=db,
        health_now=lambda: 101.0,
    )
    assert result.adapter == "sync"
    assert calls == ["sync"]
    assert result.health_skipped_adapters == ("async",)


def test_health_never_retries_same_adapter_within_one_broker_call(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    calls = {"async": 0, "sync": 0}

    def fail(name: str):
        def inner(_):
            calls[name] += 1
            raise RuntimeError("boom")
        return inner

    try:
        fetch_exact_graph(
            "conversation-1",
            adapter_fetchers={"async": fail("async"), "sync": fail("sync")},
            adapter_order=("async", "async", "sync", "sync"),
            health_db_path=db,
        )
    except Exception:
        pass
    assert calls == {"async": 1, "sync": 1}
