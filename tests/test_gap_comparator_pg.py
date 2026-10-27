from collections import Counter
import sys
from types import SimpleNamespace

from re_gpt.gap_comparator import ExactGapComparator


class Cursor:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.query = query
        assert params in (("pnf_predicate_frame",), ("thread-1",))

    def fetchall(self):
        if "information_schema" in self.query:
            return [(name,) for name in self.columns]
        return self.rows


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


def test_pg_factor_multiset_uses_bound_thread_id(monkeypatch):
    cursor = Cursor({"conversation_id", "lemma", "role", "dependency"}, [
        ("actor", "Check", "nsubj"), ("actor", "Check", "nsubj")
    ])
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(
        connect=lambda url, connect_timeout: Connection(cursor)
    ))
    comparator = ExactGapComparator(database_url="postgresql://local")
    assert comparator.load_thread_multiset_pg("thread-1") == Counter({"actor:check:nsubj": 2})


def test_pg_unreachable_falls_back_to_slrw(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(
        connect=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline"))
    ))
    comparator = ExactGapComparator(database_url="postgresql://offline")
    monkeypatch.setattr(comparator, "load_thread_multiset", lambda _: Counter({"actor:check:nsubj": 1}))
    monkeypatch.setattr(comparator, "extract_formal_multiset", lambda _: Counter())
    report = comparator.compare("thread-1", "Proof.agda")
    assert report.informal_semantic_factors == 1


def test_unset_database_url_uses_slrw(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    comparator = ExactGapComparator()
    monkeypatch.setattr(comparator, "load_thread_multiset", lambda _: Counter())
    monkeypatch.setattr(comparator, "extract_formal_multiset", lambda _: Counter())
    assert comparator.compare("thread-1", "Proof.agda").exact_multiset_match
