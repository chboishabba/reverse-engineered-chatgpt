"""Direct PostgreSQL PNF factor streaming; no .slrw dependency."""
from __future__ import annotations

from collections.abc import Iterator

def stream_postgres_pnf_corpus(dsn: str, *, batch_size: int = 1000) -> Iterator[dict]:
    """Read-only bounded-memory stream from the sole authoritative database.

    Caller must close the generator to release a partially consumed cursor.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    import psycopg
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor(name="pnf_semantic_stream") as cur:
            cur.itersize = batch_size
            cur.execute("""
                SELECT candidate_ref,batch_ref,statement_ref,role_ref,
                       source_start_char,source_end_char,surface,lemma,
                       dependency_ref,candidate_only
                  FROM pnf.statement_candidate_factor
                 ORDER BY candidate_ref
            """)
            for row in cur:
                yield dict(zip(("candidate_ref","batch_ref","statement_ref",
                                "role_ref","source_start_char","source_end_char",
                                "surface","lemma","dependency_ref","candidate_only"), row))
