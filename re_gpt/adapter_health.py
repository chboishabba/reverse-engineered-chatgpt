"""Persistent read-adapter health, cooldown and empirical latency telemetry.

This subsystem is deliberately read-plane only. It may reorder or suppress exact
conversation graph acquisition adapters, but it never schedules, retries or
replays mutation POSTs.
"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .storage import DEFAULT_DB_PATH

DEFAULT_REJECTION_BACKOFF_S = 30.0
DEFAULT_FAILURE_BACKOFF_S = 5.0
MAX_REJECTION_BACKOFF_S = 300.0
MAX_FAILURE_BACKOFF_S = 60.0
EWMA_ALPHA = 0.25
SAMPLE_RETENTION_PER_ADAPTER = 500


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


class AdapterHealthStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH, *, now: Callable[[], float] = time.time) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self.now = now
        self._init_schema()

    def __enter__(self) -> "AdapterHealthStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS adapter_health (
                    adapter TEXT PRIMARY KEY,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    rejection_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_status INTEGER,
                    last_response_class TEXT,
                    last_elapsed_ms REAL,
                    ewma_ms REAL,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS adapter_latency_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adapter TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    status INTEGER,
                    response_class TEXT
                )
            """)
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_adapter_latency_recent ON adapter_latency_samples(adapter, observed_at DESC)"
            )

    def _row(self, adapter: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM adapter_health WHERE adapter = ?", (adapter,)).fetchone()
        return dict(row) if row else None

    def record_attempt(
        self,
        adapter: str,
        *,
        elapsed_ms: float,
        status: int | None,
        response_class: str,
        rejected: bool = False,
        success: bool,
    ) -> None:
        adapter = str(adapter)
        now = float(self.now())
        prior = self._row(adapter) or {}
        success_count = int(prior.get("success_count") or 0)
        failure_count = int(prior.get("failure_count") or 0)
        rejection_count = int(prior.get("rejection_count") or 0)
        consecutive = int(prior.get("consecutive_failures") or 0)
        prior_ewma = prior.get("ewma_ms")
        elapsed = max(0.0, float(elapsed_ms))
        ewma = elapsed if prior_ewma is None else (EWMA_ALPHA * elapsed + (1.0 - EWMA_ALPHA) * float(prior_ewma))

        if success:
            success_count += 1
            consecutive = 0
            cooldown_until = 0.0
            outcome = "success"
        else:
            failure_count += 1
            consecutive += 1
            if rejected:
                rejection_count += 1
                delay = min(MAX_REJECTION_BACKOFF_S, DEFAULT_REJECTION_BACKOFF_S * (2 ** (consecutive - 1)))
                outcome = "rejected"
            else:
                delay = min(MAX_FAILURE_BACKOFF_S, DEFAULT_FAILURE_BACKOFF_S * (2 ** (consecutive - 1)))
                outcome = "failure"
            cooldown_until = now + delay

        with self.connection:
            self.connection.execute("""
                INSERT INTO adapter_health (
                    adapter, success_count, failure_count, rejection_count,
                    consecutive_failures, last_status, last_response_class,
                    last_elapsed_ms, ewma_ms, cooldown_until, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(adapter) DO UPDATE SET
                    success_count=excluded.success_count,
                    failure_count=excluded.failure_count,
                    rejection_count=excluded.rejection_count,
                    consecutive_failures=excluded.consecutive_failures,
                    last_status=excluded.last_status,
                    last_response_class=excluded.last_response_class,
                    last_elapsed_ms=excluded.last_elapsed_ms,
                    ewma_ms=excluded.ewma_ms,
                    cooldown_until=excluded.cooldown_until,
                    updated_at=excluded.updated_at
            """, (
                adapter, success_count, failure_count, rejection_count, consecutive,
                status, str(response_class), elapsed, ewma, cooldown_until, now,
            ))
            self.connection.execute("""
                INSERT INTO adapter_latency_samples(adapter, observed_at, elapsed_ms, outcome, status, response_class)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (adapter, now, elapsed, outcome, status, str(response_class)))
            self.connection.execute("""
                DELETE FROM adapter_latency_samples
                WHERE adapter = ? AND id NOT IN (
                    SELECT id FROM adapter_latency_samples WHERE adapter = ? ORDER BY observed_at DESC, id DESC LIMIT ?
                )
            """, (adapter, adapter, SAMPLE_RETENTION_PER_ADAPTER))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        now = float(self.now())
        rows = self.connection.execute("SELECT * FROM adapter_health ORDER BY adapter").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            cooldown_until = float(item.get("cooldown_until") or 0.0)
            item["cooldown_remaining_s"] = round(max(0.0, cooldown_until - now), 3)
            item["cooling_down"] = cooldown_until > now
            if item.get("ewma_ms") is not None:
                item["ewma_ms"] = round(float(item["ewma_ms"]), 3)
            result[str(item["adapter"])] = item
        return result

    def rank_adapters(self, base_order: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return admitted read adapters and currently cooled direct adapters.

        Browser remains the terminal fallback when present. Healthy direct adapters
        retain configured order; health only suppresses contexts under active
        cooldown so telemetry cannot silently redefine semantic preference.
        """
        snap = self.snapshot()
        seen: set[str] = set()
        admitted: list[str] = []
        skipped: list[str] = []
        browser: list[str] = []
        for raw in base_order:
            adapter = str(raw)
            if adapter in seen:
                continue
            seen.add(adapter)
            if adapter == "browser":
                browser.append(adapter)
                continue
            state = snap.get(adapter)
            if state and state.get("cooling_down"):
                skipped.append(adapter)
            else:
                admitted.append(adapter)
        admitted.extend(browser)
        return tuple(admitted), tuple(skipped)

    def report(self, *, sample_limit: int = 200) -> dict[str, dict[str, Any]]:
        snap = self.snapshot()
        for adapter, item in snap.items():
            rows = self.connection.execute(
                "SELECT elapsed_ms, outcome FROM adapter_latency_samples WHERE adapter = ? ORDER BY observed_at DESC, id DESC LIMIT ?",
                (adapter, max(1, int(sample_limit))),
            ).fetchall()
            values = [float(row[0]) for row in rows]
            successes = sum(1 for row in rows if str(row[1]) == "success")
            item["samples"] = len(values)
            item["successful_samples"] = successes
            item["p50_ms"] = _percentile(values, 0.50)
            item["p95_ms"] = _percentile(values, 0.95)
        return snap
