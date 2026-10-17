from __future__ import annotations

from re_gpt.adapter_health import AdapterHealthStore


def test_success_records_latency_and_clears_failures(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    with AdapterHealthStore(db, now=lambda: 100.0) as store:
        store.record_attempt("async", elapsed_ms=120.0, status=200, response_class="ok", success=True)
        snap = store.snapshot()["async"]
    assert snap["success_count"] == 1
    assert snap["consecutive_failures"] == 0
    assert snap["ewma_ms"] == 120.0
    assert snap["cooldown_remaining_s"] == 0.0


def test_rejection_enters_exponential_cooldown(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    clock = [100.0]
    with AdapterHealthStore(db, now=lambda: clock[0]) as store:
        store.record_attempt("async", elapsed_ms=50, status=429, response_class="RequestContextRejected", rejected=True, success=False)
        first = store.snapshot()["async"]
        clock[0] = 131.0
        store.record_attempt("async", elapsed_ms=55, status=429, response_class="RequestContextRejected", rejected=True, success=False)
        second = store.snapshot()["async"]
    assert 29.0 <= first["cooldown_remaining_s"] <= 30.0
    assert second["cooldown_remaining_s"] >= 59.0


def test_rank_skips_cooled_direct_adapter_but_keeps_browser_fallback(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    with AdapterHealthStore(db, now=lambda: 100.0) as store:
        store.record_attempt("async", elapsed_ms=30, status=429, response_class="RequestContextRejected", rejected=True, success=False)
        order, skipped = store.rank_adapters(("async", "sync", "browser"))
    assert order == ("sync", "browser")
    assert skipped == ("async",)


def test_latency_report_has_empirical_percentiles(tmp_path) -> None:
    db = tmp_path / "health.sqlite"
    with AdapterHealthStore(db, now=lambda: 100.0) as store:
        for value in (10.0, 20.0, 30.0, 40.0):
            store.record_attempt("async", elapsed_ms=value, status=200, response_class="ok", success=True)
        report = store.report(sample_limit=100)
    assert report["async"]["samples"] == 4
    assert report["async"]["p50_ms"] == 25.0
    assert report["async"]["p95_ms"] >= 38.0
