from __future__ import annotations

from re_gpt.global_rate_limit import GlobalBackendRateLimiter


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def test_exact_reads_reserve_global_minimum_interval(tmp_path) -> None:
    clock = Clock()
    limiter = GlobalBackendRateLimiter(tmp_path / "rate.db", now=clock.now, sleep=clock.sleep)
    first = limiter.acquire(kind="exact_read", min_interval=1.5)
    second = limiter.acquire(kind="exact_read", min_interval=1.5)
    assert first["waited_seconds"] == 0.0
    assert second["waited_seconds"] == 1.5
    assert clock.sleeps == [1.5]


def test_429_applies_exponential_global_backoff(tmp_path) -> None:
    clock = Clock()
    limiter = GlobalBackendRateLimiter(tmp_path / "rate.db", now=clock.now, sleep=clock.sleep)
    first = limiter.report_429(retry_after=None)
    assert first["backoff_level"] == 1
    assert first["backoff_seconds"] == 30.0
    second = limiter.report_429(retry_after=None)
    assert second["backoff_level"] == 2
    assert second["backoff_seconds"] == 60.0
    receipt = limiter.acquire(kind="exact_read", min_interval=1.5)
    assert receipt["waited_seconds"] == 60.0


def test_success_resets_backoff_level_without_replaying_requests(tmp_path) -> None:
    clock = Clock()
    limiter = GlobalBackendRateLimiter(tmp_path / "rate.db", now=clock.now, sleep=clock.sleep)
    limiter.report_429()
    limiter.report_success()
    snapshot = limiter.snapshot()
    assert snapshot["backoff_level"] == 0
    assert snapshot["total_429"] == 1
