"""PostgreSQL-backed exact-read adapter health.

SQLite health remains available only when a caller explicitly supplies a legacy
database path (primarily isolated tests). Production defaults use ITIR_PG_DSN.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Sequence

from .adapter_health import (
    DEFAULT_FAILURE_BACKOFF_S, DEFAULT_REJECTION_BACKOFF_S,
    EWMA_ALPHA, MAX_FAILURE_BACKOFF_S, MAX_REJECTION_BACKOFF_S,
    SAMPLE_RETENTION_PER_ADAPTER, _percentile,
)


class PostgresAdapterHealthStore:
    def __init__(self, *, connect=None, now: Callable[[], float] = time.time) -> None:
        if connect is None:
            from .pg_conversation_read import _connect
            connect = _connect
        self.connect = connect
        self.now = now
        conn = self.connect()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ops.adapter_health (
                          adapter TEXT PRIMARY KEY, success_count INTEGER NOT NULL DEFAULT 0,
                          failure_count INTEGER NOT NULL DEFAULT 0, rejection_count INTEGER NOT NULL DEFAULT 0,
                          consecutive_failures INTEGER NOT NULL DEFAULT 0, last_status INTEGER,
                          last_response_class TEXT, last_elapsed_ms DOUBLE PRECISION, ewma_ms DOUBLE PRECISION,
                          cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0, updated_at DOUBLE PRECISION NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ops.adapter_latency_samples (
                          id BIGSERIAL PRIMARY KEY, adapter TEXT NOT NULL, observed_at DOUBLE PRECISION NOT NULL,
                          elapsed_ms DOUBLE PRECISION NOT NULL, outcome TEXT NOT NULL, status INTEGER,
                          response_class TEXT
                        )
                    """)
        finally:
            conn.close()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return None

    def _row(self, adapter: str):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ops.adapter_health WHERE adapter=%s", (adapter,))
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [d[0] for d in cur.description]
                return dict(zip(columns, row))
        finally:
            conn.close()

    def record_attempt(self, adapter: str, *, elapsed_ms: float, status: int | None,
                       response_class: str, rejected: bool = False, success: bool) -> None:
        now = float(self.now()); prior = self._row(str(adapter)) or {}
        consecutive = int(prior.get("consecutive_failures") or 0)
        elapsed = max(0.0, float(elapsed_ms))
        ewma = elapsed if prior.get("ewma_ms") is None else EWMA_ALPHA * elapsed + (1-EWMA_ALPHA) * float(prior["ewma_ms"])
        if success:
            success_count = int(prior.get("success_count") or 0) + 1; failure_count = int(prior.get("failure_count") or 0)
            rejection_count = int(prior.get("rejection_count") or 0); consecutive = 0; cooldown = 0.0; outcome = "success"
        else:
            success_count = int(prior.get("success_count") or 0); failure_count = int(prior.get("failure_count") or 0) + 1
            rejection_count = int(prior.get("rejection_count") or 0) + int(rejected); consecutive += 1; outcome = "rejected" if rejected else "failure"
            base, cap = (DEFAULT_REJECTION_BACKOFF_S, MAX_REJECTION_BACKOFF_S) if rejected else (DEFAULT_FAILURE_BACKOFF_S, MAX_FAILURE_BACKOFF_S)
            cooldown = now + min(cap, base * (2 ** (consecutive - 1)))
        conn = self.connect()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO ops.adapter_health
                      (adapter,success_count,failure_count,rejection_count,consecutive_failures,last_status,last_response_class,last_elapsed_ms,ewma_ms,cooldown_until,updated_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT(adapter) DO UPDATE SET success_count=EXCLUDED.success_count,failure_count=EXCLUDED.failure_count,rejection_count=EXCLUDED.rejection_count,consecutive_failures=EXCLUDED.consecutive_failures,last_status=EXCLUDED.last_status,last_response_class=EXCLUDED.last_response_class,last_elapsed_ms=EXCLUDED.last_elapsed_ms,ewma_ms=EXCLUDED.ewma_ms,cooldown_until=EXCLUDED.cooldown_until,updated_at=EXCLUDED.updated_at""",
                      (str(adapter),success_count,failure_count,rejection_count,consecutive,status,str(response_class),elapsed,ewma,cooldown,now))
                    cur.execute("INSERT INTO ops.adapter_latency_samples(adapter,observed_at,elapsed_ms,outcome,status,response_class) VALUES (%s,%s,%s,%s,%s,%s)", (str(adapter),now,elapsed,outcome,status,str(response_class)))
                    cur.execute("""DELETE FROM ops.adapter_latency_samples WHERE adapter=%s AND id NOT IN
                      (SELECT id FROM ops.adapter_latency_samples WHERE adapter=%s ORDER BY observed_at DESC,id DESC LIMIT %s)""", (str(adapter),str(adapter),SAMPLE_RETENTION_PER_ADAPTER))
        finally:
            conn.close()

    def snapshot(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ops.adapter_health ORDER BY adapter")
                cols = [d[0] for d in cur.description]; rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        finally: conn.close()
        now = float(self.now()); out = {}
        for item in rows:
            item["cooldown_remaining_s"] = round(max(0.0, float(item.get("cooldown_until") or 0)-now), 3)
            item["cooling_down"] = float(item.get("cooldown_until") or 0) > now
            out[str(item["adapter"])] = item
        return out

    def rank_adapters(self, base_order: Sequence[str]):
        snap=self.snapshot(); admitted=[]; skipped=[]; seen=set()
        for adapter in base_order:
            if adapter in seen: continue
            seen.add(adapter)
            if adapter == "browser": admitted.append(adapter)
            elif snap.get(adapter, {}).get("cooling_down"): skipped.append(adapter)
            else: admitted.append(adapter)
        return tuple(admitted), tuple(skipped)

    def report(self, *, sample_limit=200):
        out=self.snapshot(); conn=self.connect()
        try:
            with conn.cursor() as cur:
                for adapter,item in out.items():
                    cur.execute("SELECT elapsed_ms,outcome FROM ops.adapter_latency_samples WHERE adapter=%s ORDER BY observed_at DESC,id DESC LIMIT %s", (adapter,max(1,int(sample_limit))))
                    rows=cur.fetchall(); vals=[float(row[0]) for row in rows]
                    item.update(samples=len(vals), successful_samples=sum(str(row[1])=="success" for row in rows), p50_ms=_percentile(vals,.5), p95_ms=_percentile(vals,.95))
        finally: conn.close()
        return out

    def reset(self, adapter: str | None = None):
        conn=self.connect()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    if adapter:
                        cur.execute("DELETE FROM ops.adapter_health WHERE adapter=%s", (adapter,)); cur.execute("DELETE FROM ops.adapter_latency_samples WHERE adapter=%s", (adapter,))
                    else:
                        cur.execute("TRUNCATE ops.adapter_health, ops.adapter_latency_samples")
        finally: conn.close()
