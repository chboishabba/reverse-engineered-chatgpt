"""PostgreSQL-only durable rejuvenation coordinator.

The external ChatGPT send is never performed inside a database transaction.
A lease and reconciliation state prevent automatic duplicate sends after an
ambiguous network outcome. No SQLite or .slrw fallback exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable, Iterable, Mapping

from .semantic_rejuvenation import Factor, extract_factors, state_capsule

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.rejuvenation_job (
    job_key text PRIMARY KEY,
    parent_thread text NOT NULL,
    prompt_hash text NOT NULL,
    capsule text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('prepared','sending','needs_reconciliation','complete')),
    child_thread text UNIQUE,
    factor_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'complete') = (child_thread IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS rejuvenation_job_parent_idx
    ON ops.rejuvenation_job(parent_thread);
"""

@dataclass(frozen=True)
class Prepared:
    key: str
    capsule: str
    state: str

class PostgresRejuvenation:
    """A DB-API 2.0 PostgreSQL connection factory is injected by the caller.

    Each method owns one connection and one atomic transaction. A caller must
    supply a PostgreSQL driver (for example psycopg); this module does not
    import any embedded database.
    """
    def __init__(self, connect: Callable[[], object]):
        self.connect = connect

    def install(self) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)

    def prepare(self, parent: str, prompt: str,
                turns: Iterable[Mapping[str, object]]) -> Prepared:
        factors = extract_factors(turns)
        capsule = state_capsule(factors)
        prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()
        key = sha256(json.dumps([parent, prompt_hash], separators=(",", ":")).encode()).hexdigest()
        payload = json.dumps([dict(subject=f.subject, predicate=f.predicate,
                                   object=f.object, source_turn=f.source_turn,
                                   evidence=f.evidence, digest=f.digest)
                              for f in factors], ensure_ascii=False)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ops.rejuvenation_job
                      (job_key,parent_thread,prompt_hash,capsule,state,factor_payload)
                    VALUES (%s,%s,%s,%s,'prepared',%s::jsonb)
                    ON CONFLICT (job_key) DO NOTHING
                """, (key, parent, prompt_hash, capsule, payload))
                cur.execute("SELECT capsule,state FROM ops.rejuvenation_job WHERE job_key=%s", (key,))
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("rejuvenation job was not persisted")
        return Prepared(key, row[0], row[1])

    def claim(self, key: str) -> bool:
        """Only a prepared job may be claimed. Never auto-retry a sent job."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ops.rejuvenation_job
                       SET state='sending',updated_at=now()
                     WHERE job_key=%s AND state='prepared'
                    RETURNING job_key
                """, (key,))
                return cur.fetchone() is not None

    def finish(self, key: str, child: str) -> None:
        if not child:
            raise ValueError("child conversation id required")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ops.rejuvenation_job
                       SET state='complete',child_thread=%s,updated_at=now()
                     WHERE job_key=%s AND state IN ('sending','needs_reconciliation')
                    RETURNING job_key
                """, (child, key))
                if cur.fetchone() is None:
                    raise RuntimeError("invalid completion transition")

    def require_reconciliation(self, key: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ops.rejuvenation_job
                       SET state='needs_reconciliation',updated_at=now()
                     WHERE job_key=%s AND state='sending'
                """, (key,))

    def dispatch(self, key: str, send: Callable[[str], str]) -> str | None:
        """Caller supplies authenticated async transport through an adapter.

        This synchronous adapter deliberately does not assume the remote
        transport supports idempotency. If sending fails, a human or an exact
        remote-history reconciliation must resolve it before another send.
        """
        if not self.claim(key):
            return None
        try:
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT capsule FROM ops.rejuvenation_job WHERE job_key=%s", (key,))
                    capsule = cur.fetchone()[0]
            child = send(capsule)
            self.finish(key, child)
            return child
        except BaseException:
            self.require_reconciliation(key)
            raise
