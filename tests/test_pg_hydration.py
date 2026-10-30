"""Hydration contract tests: roster data is PG-only, never an implicit SQLite write."""
from unittest.mock import Mock
import pytest
from re_gpt import pg_hydration


class Cursor:
    def __init__(self):
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class Connection:
    def __init__(self):
        self.cur = Cursor()
    def cursor(self):
        return self.cur
    def transaction(self):
        return self.cur
    def close(self):
        pass


def test_roster_persists_identity_without_claiming_hydration():
    conn = Connection()
    pg_hydration.record_roster([{"id": "cid", "title": "Title", "update_time": 123}],
                               connect=lambda: conn)
    statements = [sql for sql, _ in conn.cur.calls]
    assert any("ops.chat_hydration_roster" in sql for sql in statements)
    assert all("sqlite" not in sql.lower() for sql in statements)
    assert any("VALUES (%s,%s,%s,'queued')" in sql for sql in statements)


def test_roster_bounds_fail_closed():
    with pytest.raises(ValueError):
        pg_hydration.roster_page(pages=0)
    with pytest.raises(ValueError):
        pg_hydration.hydrate_pending(limit=0)


def test_graph_validation_blocks_empty_import():
    with pytest.raises(ValueError):
        pg_hydration._persist_graph(Connection(), "cid", {"mapping": {}})
