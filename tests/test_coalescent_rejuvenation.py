"""Deterministic coalescence discovery and attribution tests."""
import pytest
from re_gpt.postgres_coalescence import discover_coalescence_clusters, build_coalesced_world_capsule

class Cursor:
    def __init__(self, responses): self.responses=responses; self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,args): self.calls.append((sql,args))
    def fetchall(self): return self.responses.pop(0)
class DB:
    def __init__(self,responses): self.cur=Cursor(responses)
    def cursor(self): return self.cur

def manifold():
    return {"conversations": {
        c: dict(primary_topic_id="biology",sub_eigen_cluster="flux",
                lambda_1=3,lambda_2=2,lambda_3=1,
                active_goals=["prove boundary flux"])
        for c in ("a","b","c")
    }}

def test_complete_link_and_shared_deliverable():
    db=DB([[("conversation:a","flux"),("conversation:a","transport"),
            ("conversation:b","flux"),("conversation:b","transport"),
            ("conversation:c","governance")]])
    clusters=discover_coalescence_clusters(db,["a","b","c"],manifold_summary=manifold())
    assert len(clusters)==1
    assert clusters[0].parents==("a","b")
    assert clusters[0].similarity==1
    assert "pnf.statement_candidate_factor" in db.cur.calls[0][0]

def test_capsule_retains_both_parent_provenances():
    db=DB([
        [("node:a","user","Transport requires diffusion.","2026-10-29T00:00:00Z")],
        [("candidate:world","statement:w","obj","diffusion","message:world:node")],
        [("node:b","user","Flux requires diffusion.","2026-10-29T00:00:00Z")],
        [("candidate:world","statement:w","obj","diffusion","message:world:node")],
    ])
    capsule=build_coalesced_world_capsule(db,["b","a"],"biology",manifold_summary=manifold())
    assert "conversation:a" in capsule and "conversation:b" in capsule
    assert "prove boundary flux" in capsule
    assert "candidate:world" in capsule

def test_cross_topic_fails_closed():
    summary=manifold()
    summary["conversations"]["b"]["primary_topic_id"]="legal"
    with pytest.raises(ValueError,match="topic mismatch"):
        build_coalesced_world_capsule(DB([]),["a","b"],"biology",manifold_summary=summary)

def test_missing_goals_blocks_merging():
    summary=manifold()
    summary["conversations"]["a"]["active_goals"]=[]
    with pytest.raises(ValueError,match="shared explicit deliverable"):
        build_coalesced_world_capsule(DB([]),["a","b"],"biology",manifold_summary=summary)
