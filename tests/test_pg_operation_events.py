"""Offline contract tests for immutable conversation-scoped event replay."""
import pytest
from re_gpt.pg_operation_events import append_event, events_after, EventConflict

class Cursor:
    def __init__(self, rows):
        self.rows=list(rows); self.executed=[]
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def execute(self,sql,params=None): self.executed.append((sql,params))
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def fetchall(self): return self.rows

class Connection:
    def __init__(self, rows): self.cur=Cursor(rows); self.closed=False
    def cursor(self): return self.cur
    def transaction(self): return self.cur
    def close(self): self.closed=True

def test_append_binds_identity_and_body():
    c=Connection([(7,)])
    answer=append_event("thread-A","operation-A",1,"token_delta",{"delta":"yes"},connect=lambda:c)
    assert answer=={"event_id":7,"inserted":True}
    assert any("ON CONFLICT" in sql for sql,_ in c.cur.executed)
    assert any("thread-A" in str(params) for _,params in c.cur.executed)
    assert c.closed

def test_conflicting_duplicate_rejected():
    c=Connection([None,(7,"thread-B","token_delta",{"delta":"other"})])
    with pytest.raises(EventConflict):
        append_event("thread-A","operation-A",1,"token_delta",{"delta":"yes"},connect=lambda:c)

def test_replay_scopes_thread_and_cursor():
    c=Connection([(9,"op",1,"tool_result",{"body":"real"}, "now")])
    result=events_after("thread-A",after=8,connect=lambda:c)
    assert result["next_cursor"]==9
    assert result["events"][0]["payload"]["body"]=="real"
    sql,params=c.cur.executed[0]
    assert params==("thread-A",8,100)

def test_rejects_bad_cursor():
    with pytest.raises(ValueError): events_after("thread-A",after=-1)
