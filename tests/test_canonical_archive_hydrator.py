import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re_gpt.canonical_archive_hydrator import canonical_archive_path, hydrate_verified_graph

class FakeProducer:
    def __init__(self):
        self.calls=[]
        self.rows=[]
    def _extract_messages_for_ingest(self,graph,*,extract_text):
        return [dict(row) for row in self.rows]
    def _load_structurer_ingest(self):
        return self
    def ingest_parsed_messages(self,rows,**kwargs):
        self.calls.append((rows,kwargs))
        return {"inserted":len(rows),"duplicates":0}

def row(text="lemma"):
    return {"content":text,"source_message_id":"message-1","node_id":"node-1",
            "parent_node_id":"parent-1","branch_membership":"active",
            "created_at":1.0,"role":"assistant","thread_title":"proof"}

class HydrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive=Path(self.tmp.name)/"canonical.sqlite"
        sqlite3.connect(self.archive).close()
        self.producer=FakeProducer()
        self.producer.rows=[row()]
        self.graph={"conversation_id":"thread-1","mapping":{}}
    def hydrate(self):
        return hydrate_verified_graph(self.graph,conversation_id="thread-1",
            account="account-1",archive_db=self.archive,
            producer=self.producer,parser=lambda _: "")
    def test_replay_no_duplicate_ingest(self):
        first=self.hydrate()
        second=self.hydrate()
        self.assertEqual(first["new_identities"],1)
        self.assertEqual(first["ingest_inserted"],1)
        self.assertEqual(second["new_identities"],0)
        self.assertEqual(second["replayed"],1)
        self.assertEqual(len(self.producer.calls),1)
    def test_changed_digest_conflict_no_overwrite(self):
        self.hydrate()
        self.producer.rows=[row("changed")]
        conflict=self.hydrate()
        self.assertEqual(conflict["conflicts"],1)
        self.assertEqual(len(self.producer.calls),1)
        with sqlite3.connect(self.archive) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM graph_archive_hydration_conflict").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT body_sha256 FROM graph_archive_hydration_receipt").fetchone()[0],
                             hashlib.sha256(b"lemma").hexdigest())
    def test_identity_mismatch_rejected_without_ingest(self):
        with self.assertRaises(ValueError):
            hydrate_verified_graph(self.graph,conversation_id="different",
                account="account-1",archive_db=self.archive,producer=self.producer)
        self.assertEqual(self.producer.calls,[])
    def test_canonical_default_and_explicit_override(self):
        with patch.dict("os.environ",{},clear=True):
            self.assertEqual(canonical_archive_path(),Path.home()/"chat_archive.sqlite")
        with patch.dict("os.environ",{"ITIR_CANONICAL_CHAT_ARCHIVE":str(self.archive)}):
            self.assertEqual(canonical_archive_path(),self.archive)
            self.assertEqual(canonical_archive_path("~/selected.sqlite"),
                             Path.home()/"selected.sqlite")

if __name__=="__main__":
    unittest.main()
