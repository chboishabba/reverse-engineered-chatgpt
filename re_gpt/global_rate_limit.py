"""Cross-process SQLite pacing for ChatGPT backend-api requests."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable

from .storage import DEFAULT_DB_PATH

DEFAULT_BACKEND_MIN_INTERVAL = float(os.environ.get("RE_GPT_BACKEND_MIN_INTERVAL", "0.25"))
DEFAULT_EXACT_READ_MIN_INTERVAL = float(os.environ.get("RE_GPT_EXACT_READ_MIN_INTERVAL", "1.5"))
DEFAULT_429_BASE_BACKOFF = float(os.environ.get("RE_GPT_429_BASE_BACKOFF", "30"))
DEFAULT_429_MAX_BACKOFF = float(os.environ.get("RE_GPT_429_MAX_BACKOFF", "300"))


class GlobalBackendRateLimiter:
    """One global backend-api dispatch clock shared by every local process."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.sleep = sleep
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS backend_rate_limit (
                    queue_key TEXT PRIMARY KEY,
                    next_allowed_at REAL NOT NULL DEFAULT 0,
                    last_dispatch_at REAL,
                    backoff_level INTEGER NOT NULL DEFAULT 0,
                    total_dispatches INTEGER NOT NULL DEFAULT 0,
                    total_429 INTEGER NOT NULL DEFAULT 0,
                    last_status INTEGER,
                    updated_at REAL NOT NULL
                )
            """)
            connection.execute(
                "INSERT OR IGNORE INTO backend_rate_limit (queue_key, updated_at) VALUES ('backend-api', ?)",
                (self.now(),),
            )

    def _reserve(self, min_interval: float) -> float:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM backend_rate_limit WHERE queue_key='backend-api'"
            ).fetchone()
            now = float(self.now())
            next_allowed = float(row["next_allowed_at"] or 0.0) if row else 0.0
            dispatch_at = max(now, next_allowed)
            connection.execute(
                """
                UPDATE backend_rate_limit
                SET next_allowed_at=?, last_dispatch_at=?, total_dispatches=total_dispatches+1, updated_at=?
                WHERE queue_key='backend-api'
                """,
                (dispatch_at + max(0.0, float(min_interval)), dispatch_at, now),
            )
            connection.commit()
            return max(0.0, dispatch_at - now)
        finally:
            connection.close()

    def acquire(
        self,
        *,
        kind: str = "backend",
        min_interval: float | None = None,
    ) -> dict[str, Any]:
        interval = (
            DEFAULT_EXACT_READ_MIN_INTERVAL
            if kind == "exact_read" and min_interval is None
            else DEFAULT_BACKEND_MIN_INTERVAL if min_interval is None else float(min_interval)
        )
        wait = self._reserve(interval)
        if wait > 0:
            self.sleep(wait)
        return {
            "kind": kind,
            "minimum_interval_seconds": interval,
            "waited_seconds": round(wait, 6),
        }

    def report_429(self, *, retry_after: float | None = None) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM backend_rate_limit WHERE queue_key='backend-api'"
            ).fetchone()
            now = float(self.now())
            prior_level = int(row["backoff_level"] or 0) if row else 0
            level = min(prior_level + 1, 16)
            computed = min(DEFAULT_429_MAX_BACKOFF, DEFAULT_429_BASE_BACKOFF * (2 ** (level - 1)))
            if retry_after is not None:
                computed = max(computed, max(0.0, float(retry_after)))
            current_next = float(row["next_allowed_at"] or 0.0) if row else 0.0
            next_allowed = max(current_next, now + computed)
            connection.execute(
                """
                UPDATE backend_rate_limit
                SET next_allowed_at=?, backoff_level=?, total_429=total_429+1,
                    last_status=429, updated_at=? WHERE queue_key='backend-api'
                """,
                (next_allowed, level, now),
            )
            connection.commit()
            return {
                "backoff_level": level,
                "backoff_seconds": float(computed),
                "next_allowed_at": next_allowed,
            }
        finally:
            connection.close()

    def report_success(self, *, status: int = 200) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE backend_rate_limit
                SET backoff_level=0, last_status=?, updated_at=? WHERE queue_key='backend-api'
                """,
                (int(status), float(self.now())),
            )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM backend_rate_limit WHERE queue_key='backend-api'"
            ).fetchone()
        if row is None:
            return {}
        now = float(self.now())
        result = dict(row)
        result["wait_remaining_seconds"] = round(max(0.0, float(result.get("next_allowed_at") or 0.0) - now), 6)
        return result
