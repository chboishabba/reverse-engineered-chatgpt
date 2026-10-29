"""SQL mapping and rollback contract checks without a live database."""
import pytest
from re_gpt.semantic_rejuvenation import Factor
from re_gpt.postgres_semantic_registration import register_thread_factors_and_messages

class Cursor:
    def __init__(self, db): self.db = db
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params):
        self.db.calls.append((sql, params))
        if self.db.fail_on and self.db.fail_on in sql:
            raise ConnectionError("injected SQL fault")

class Connection:
    autocommit = False
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []
        self.committed = False
        self.rolled_back = False
    def __enter__(self): return self
    def __exit__(self, kind, exc, tb):
        self.committed = kind is None
        self.rolled_back = kind is not None
        return False
    def cursor(self): return Cursor(self)

TURN = dict(message_index=0,node_id="node-1",parent_node_id=None,
            message_time_ref="2026-10-29T00:00:00Z",author="user",
            content="Thread requires PostgreSQL.")
FACTOR = Factor("Thread","requires","PostgreSQL",0)

def test_schema_mapping_and_authority_flags():
    db = Connection()
    counts = register_thread_factors_and_messages(db,"thread-1","Title",[TURN],[FACTOR])
    assert counts == {"messages": 1, "factors": 3, "products": 1}
    assert db.committed and not db.rolled_back
    assert len(db.calls) == 5
    assert "corpus.chat_archive_message" in db.calls[0][0]
    assert db.calls[0][1][0] == "message:thread-1:node-1"
    assert "false,false,false" in db.calls[0][0]
    assert all("pnf.statement_candidate_factor" in x[0] for x in db.calls[1:4])
    assert db.calls[1][1][3:7] == ("subj", 0, 6, "Thread")
    assert "pnf.candidate_semantic_product" in db.calls[4][0]

def test_atomic_rollback_on_injected_pg_fault():
    db = Connection(fail_on="pnf.candidate_semantic_product")
    with pytest.raises(ConnectionError):
        register_thread_factors_and_messages(db,"thread-1","Title",[TURN],[FACTOR])
    assert db.rolled_back and not db.committed

def test_missing_exact_node_fails_closed():
    db = Connection()
    with pytest.raises(ValueError, match="node_id"):
        register_thread_factors_and_messages(db,"thread-1","Title",
            [{k:v for k,v in TURN.items() if k != "node_id"}],[])
    assert not db.calls

def test_autocommit_forbidden():
    db = Connection()
    db.autocommit = True
    with pytest.raises(ValueError, match="autocommit"):
        register_thread_factors_and_messages(db,"thread-1","Title",[TURN],[])
