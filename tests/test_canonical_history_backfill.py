"""Offline fixtures for the canonical history backfill; no network or mutation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re_gpt.canonical_history_backfill import backfill_history

class Producer:
    def _load_structurer_ingest(self):
        return self

    def ingest_parsed_messages(self, rows, *, db_path, platform, account_id, **kwargs):
        with sqlite3.connect(db_path) as db:
            for row in rows:
                db.execute("""INSERT INTO messages
                    (platform,account_id,source_thread_id,source_message_id,node_id,text)
                    VALUES (?,?,?,?,?,?)""",
                    (platform,account_id,row["thread_id"],row["source_message_id"],
                     row["node_id"],row["content"]))
        return {"inserted":len(rows),"duplicates":0}

class BackfillTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.history=Path(tmp.name)/"history.sqlite"
        self.archive=Path(tmp.name)/"archive.sqlite"
        with sqlite3.connect(self.history) as db:
            db.execute("""CREATE TABLE conversations
                (conversation_id TEXT PRIMARY KEY,title TEXT)""")
            db.execute("""CREATE TABLE messages (
                conversation_id TEXT,message_index INTEGER,author TEXT,
                content TEXT,create_time REAL,message_key TEXT)""")
            db.execute("INSERT INTO conversations VALUES ('thread','Proof')")
            db.execute("INSERT INTO messages VALUES ('thread',0,'user','lemma',1.0,'node')")
        with sqlite3.connect(self.archive) as db:
            db.execute("""CREATE TABLE messages (
                platform TEXT,account_id TEXT,source_thread_id TEXT,
                source_message_id TEXT,node_id TEXT,text TEXT)""")
        self.producer=Producer()

    def run_backfill(self,batch_size=1):
        return backfill_history(history_db=self.history,archive_db=self.archive,
            account="account",batch_size=batch_size,producer=self.producer)

    def test_real_live_schema_first_run_and_replay(self):
        live_columns = (
            "message_id", "canonical_thread_id", "platform", "account_id",
            "ts", "role", "text", "title", "source_id", "source_thread_id",
            "source_message_id", "source_path", "source_bucket", "provenance_json",
            "node_id", "parent_node_id", "branch_membership", "tool_kind",
            "citation_token", "filename", "mime_type", "asset_pointer",
            "body_storage_ref", "source_scope", "visibility",
        )
        with sqlite3.connect(self.archive) as db:
            db.execute("DROP TABLE messages")
            db.execute("CREATE TABLE messages (" +
                       ",".join(f"{column} TEXT" for column in live_columns) + ")")
            self.assertEqual(
                {r[1] for r in db.execute("PRAGMA table_info(messages)")},
                set(live_columns),
            )
        first = self.run_backfill()
        self.assertEqual(first["messages_ingested"], 1)
        self.assertEqual(first["replayed"], 0)
        second = self.run_backfill()
        self.assertEqual(second["messages_ingested"], 0)
        self.assertEqual(second["replayed"], 1)
        with sqlite3.connect(self.archive) as db:
            self.assertEqual(db.execute("SELECT text FROM messages").fetchone()[0], "lemma")

    def test_first_run_then_replay(self):
        first=self.run_backfill()
        self.assertEqual(first["messages_ingested"],1)
        second=self.run_backfill()
        self.assertEqual(second["messages_ingested"],0)
        self.assertEqual(second["replayed"],1)
        with sqlite3.connect(self.archive) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0],1)

    def test_changed_digest_conflict(self):
        self.run_backfill()
        with sqlite3.connect(self.history) as db:
            db.execute("UPDATE messages SET content='changed'")
        result=self.run_backfill()
        self.assertEqual(result["conflicts"],1)
        with sqlite3.connect(self.archive) as db:
            self.assertEqual(db.execute("SELECT text FROM messages").fetchone()[0],"lemma")
            self.assertEqual(db.execute("SELECT count(*) FROM graph_archive_hydration_conflict").fetchone()[0],1)

    def test_existing_other_ingest(self):
        with sqlite3.connect(self.archive) as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                ("chatgpt","account","thread","node","node","lemma"))
        result=self.run_backfill()
        self.assertEqual(result["messages_ingested"],0)
        self.assertEqual(result["skipped_as_already_present"],1)
        self.assertEqual(self.run_backfill()["replayed"],1)

    def test_resume_batches(self):
        with sqlite3.connect(self.history) as db:
            db.execute("INSERT INTO messages VALUES ('thread',1,'assistant','proof',2.0,'node2')")
        first=self.run_backfill(batch_size=1)
        self.assertEqual(first["messages_ingested"],2)
        second=self.run_backfill(batch_size=1)
        self.assertEqual(second["replayed"],2)
        self.assertEqual(second["conversations_scanned"],1)

    def test_account_required(self):
        with patch.dict("os.environ",{},clear=True):
            with self.assertRaises(ValueError):
                backfill_history(history_db=self.history,archive_db=self.archive,
                    producer=self.producer)

if __name__=="__main__":
    unittest.main()
