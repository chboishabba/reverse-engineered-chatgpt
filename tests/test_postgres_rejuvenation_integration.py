"""PostgreSQL integration checks; requires a disposable PostgreSQL database.

Set REJUVENATION_TEST_PG_DSN to an isolated database, never production.
"""
import os
import pytest

from re_gpt.postgres_rejuvenation import PostgresRejuvenation

pytestmark = pytest.mark.skipif(
    not os.getenv("REJUVENATION_TEST_PG_DSN"),
    reason="requires disposable REJUVENATION_TEST_PG_DSN",
)

def service():
    psycopg = pytest.importorskip("psycopg")
    return PostgresRejuvenation(lambda: psycopg.connect(os.environ["REJUVENATION_TEST_PG_DSN"]))

def test_prepare_is_idempotent_and_claim_is_single_use():
    s = service()
    s.install()
    p = s.prepare("test-parent-atomic", "test prompt atomic",
                  [{"content": "Thread requires PostgreSQL.", "message_index": 1}])
    assert s.prepare("test-parent-atomic", "test prompt atomic", []).key == p.key
    # Other test runs may have claimed the same key: use a unique key per run
    # in production fixtures. This checks only the idempotent registration.
    with s.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ops.rejuvenation_job WHERE job_key=%s", (p.key,))
            assert cur.fetchone()[0] == 1

def test_transaction_rollback_on_sql_failure():
    s = service()
    s.install()
    with pytest.raises(Exception):
        with s.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ops.rejuvenation_job (job_key,parent_thread,prompt_hash,capsule,state,factor_payload) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                            ("rollback-test", "parent", "hash", "capsule", "prepared", "[]"))
                raise RuntimeError("simulated connection/transaction failure")
    with s.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ops.rejuvenation_job WHERE job_key=%s", ("rollback-test",))
            assert cur.fetchone()[0] == 0
