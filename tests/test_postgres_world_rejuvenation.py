"""Deterministic PostgreSQL world rejuvenation tests (no external DB)."""
import pytest
from re_gpt.postgres_world_rejuvenation import (
    load_postgres_processing_queue, build_world_enhanced_capsule,
    allocate_topic_workers,
)

class Cursor:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params):
        self.calls.append((sql, params))
    def fetchall(self):
        return self.responses.pop(0)

class Connection:
    def __init__(self, responses): self.cur = Cursor(responses)
    def cursor(self): return self.cur

def test_queue_queries_only_postgres_active_candidates():
    db = Connection([[("abc",), ("def",)]])
    assert load_postgres_processing_queue(db, prefix="a", limit=20) == ["abc", "def"]
    sql, args = db.cur.calls[0]
    assert "corpus.chat_archive_message" in sql
    assert "branch_membership_ref = 'active'" in sql
    assert "candidate_only = true" in sql
    assert args == ("a", "a", 20)

def test_world_capsule_is_topic_specific_and_uses_pg_adjacent_lemmas():
    db = Connection([
        [("node:1", "user", "Transport requires diffusion.", "2026-10-29T01:00:00Z")],
        [("candidate:x", "statement:x", "obj", "diffusion", "message:other:node")],
    ])
    summary = {"conversations": {"abc": {
        "primary_topic_id": "biology", "sub_eigen_cluster": "transport",
        "lambda_1": 3.0, "lambda_2": 2.0, "lambda_3": 1.0,
        "active_goals": ["Validate membrane transport constraints"],
    }}}
    capsule = build_world_enhanced_capsule(db, "abc", summary)
    assert "Transport requires diffusion" in capsule
    assert "Primary topic: biology" in capsule
    assert "Sub-eigen cluster: transport" in capsule
    assert "Validate membrane transport constraints" in capsule
    assert "candidate:x" in capsule
    assert "pnf.statement_candidate_factor" in db.cur.calls[1][0]
    assert "message:abc:%" == db.cur.calls[1][1][1]

def test_missing_manifold_fails_closed():
    with pytest.raises(ValueError, match="missing from manifold"):
        build_world_enhanced_capsule(Connection([]), "unknown", {"conversations": {}})

def test_pareto_topic_width_capacity_and_eligibility():
    topics = [
        dict(topic_id="blocked", alpha=9, total_goals=50, unblocked_goals=46,
             shape="frontier_blocked", blocker_count=4, pareto_eligible=True),
        dict(topic_id="sequential", alpha=8, total_goals=10, unblocked_goals=10,
             shape="sequential", pareto_eligible=True),
        dict(topic_id="parallel", alpha=7, total_goals=10, unblocked_goals=8,
             shape="parallel", sub_eigen_dimension=5, pareto_eligible=True),
        dict(topic_id="ineligible", alpha=6, total_goals=10, unblocked_goals=10,
             shape="parallel", sub_eigen_dimension=15, pareto_eligible=False),
    ]
    result = allocate_topic_workers(topics, capacity=20)
    assert [result[k]["workers"] for k in ("blocked","sequential","parallel","ineligible")] == [4,1,5,0]
    assert result["blocked"]["score"] == pytest.approx(9 * 46 / 50 * 4)
    assert sum(v["workers"] for v in result.values()) <= 20
