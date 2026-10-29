"""Bounded automatic formal capsule regression tests."""
from pathlib import Path
from re_gpt.automatic_formal_evidence import automatic_formal_evidence

class Cursor:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,params): self.sql=sql; self.params=params
    def fetchall(self): return [("subj","transport","message:thread:node")]
class DB:
    def cursor(self): return Cursor()

def test_gap_search_and_preflight_diagnostic(tmp_path,monkeypatch):
    p=tmp_path/"DASHI"/"Transport.agda"
    p.parent.mkdir()
    p.write_text("module DASHI.Transport where\ntransport : Set\ntransport = Set\n")
    calls=[]
    def fake_run(argv,cwd,timeout):
        calls.append(argv)
        if argv[0]=="rg":
            return {"status":"ok","output":"DASHI/Transport.agda:2:transport : Set"}
        return {"status":"failed","exit_code":1,"output":"scope warning: missing import"}
    monkeypatch.setattr("re_gpt.automatic_formal_evidence._run",fake_run)
    text=automatic_formal_evidence(DB(),"thread",{"formal_files":["DASHI/Transport.agda"]},repo_root=tmp_path)
    assert "Unformalized premises" in text
    assert "Unaddressed formal tokens" in text
    assert "DASHI/Transport.agda:2" in text
    assert "scope warning: missing import" in text
    assert any(x[0]=="rg" for x in calls)
    assert any(x[0]=="dashi-agda-preflight" for x in calls)

def test_missing_and_unsafe_formal_paths_are_explicit(tmp_path):
    text=automatic_formal_evidence(DB(),"thread",
        {"formal_files":["../escape.agda","DASHI/NotFound.agda","notes.txt"]},
        repo_root=tmp_path)
    assert text.count("unavailable/invalid")==3
    assert "kernel_verified" not in text

def test_no_formal_target_never_claims_verification(tmp_path):
    text=automatic_formal_evidence(DB(),"thread",{},repo_root=tmp_path)
    assert "no formal claim" in text

def test_git_and_pr_lineage_included(tmp_path, monkeypatch):
    p = tmp_path / "DASHI" / "Test.agda"
    p.parent.mkdir()
    p.write_text("module DASHI.Test where\n")
    def fake_run(argv, cwd, timeout):
        if argv[0] == "git":
            return {"status": "ok", "output": "abc1234 feat: recent commit message\n"}
        if argv[0] == "gh":
            return {"status": "ok", "output": "#53 AO: sublinear topic metasystem\n"}
        return {"status": "ok", "output": ""}
    monkeypatch.setattr("re_gpt.automatic_formal_evidence._run", fake_run)
    text = automatic_formal_evidence(DB(), "thread", {"formal_files": ["DASHI/Test.agda"]}, repo_root=tmp_path)
    assert "Git Lineage & Active PR Context" in text
    assert "abc1234 feat: recent commit message" in text
    assert "#53 AO: sublinear topic metasystem" in text

