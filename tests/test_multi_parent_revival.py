from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re_gpt.multi_parent_revival import record_multi_parent_lineage, revive_from_handoff


class FakeMutationService:
    created_state = "complete"
    attached_state = "complete"

    def __init__(self, **kwargs):
        self.new_calls = []
        self.continue_calls = []

    def new_conversation(self, prompt, **kwargs):
        self.new_calls.append((prompt, kwargs))
        return {"state": self.created_state, "conversation_id": "child-1",
                "operation_id": "create-op"}

    def continue_thread(self, conversation_id, prompt, **kwargs):
        self.continue_calls.append((conversation_id, prompt, kwargs))
        return {"state": self.attached_state, "operation_id": "attach-op",
                "attachment_ids": ["file-1"], "canonical_attachment_count": 1,
                "user_node_id": "user-node-1"}


class MultiParentRevivalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "operations.sqlite"
        self.artifact = Path(self.temp.name) / "handoff.json"
        self.artifact.write_text('{"version":"itir.pnf.multi_parent_handoff.v1","parents":[{"canonical_thread_id":"canonical-p1"},{"canonical_thread_id":"canonical-p2"}],"candidate_world":{"status":"candidate_only","reviewed_world_model":false,"formal_proof":false,"cross_parent_equivalences":[]},"formal_gap":{"selected_context_sufficiency_proved":false},"authority":{"dispatch":false,"equivalence":"not_established"}}')
        self.parents = ["canonical-p1", "canonical-p2"]

    def test_lineage_receipt_preserves_ordered_independent_parents(self):
        receipt = record_multi_parent_lineage(
            self.db, "child-1", self.parents, handoff_path=self.artifact,
            handoff_sha256="a" * 64)
        self.assertEqual(receipt["canonical_parent_thread_ids"], self.parents)
        with sqlite3.connect(self.db) as db:
            rows = db.execute(
                "SELECT parent_ordinal,canonical_parent_thread_id "
                "FROM conversation_lineage_multi_parent_edges "
                "WHERE child_conversation_id=? ORDER BY parent_ordinal", ("child-1",)
            ).fetchall()
        self.assertEqual(rows, [(0, self.parents[0]), (1, self.parents[1])])

    def test_create_once_then_attach_with_owned_async_receipt(self):
        with patch("re_gpt.multi_parent_revival.MutationService", FakeMutationService):
            result = revive_from_handoff(self.parents, self.artifact, "continue work", db_path=self.db)
        self.assertEqual(result["child_conversation_id"], "child-1")
        self.assertEqual(result["canonical_parent_thread_ids"], self.parents)
        self.assertTrue(result["attachment_receipt"]["verified_by_mutation_service"])
        self.assertEqual(result["attachment_receipt"]["operation_id"], "attach-op")
        self.assertFalse(result["automatic_resend_allowed"])
        with sqlite3.connect(self.db) as db:
            state = db.execute(
                "SELECT attachment_state FROM conversation_lineage_multi_parent "
                "WHERE child_conversation_id='child-1'").fetchone()[0]
        self.assertEqual(state, "complete")

    def test_uncertain_creation_does_not_attach_or_record_child(self):
        class Uncertain(FakeMutationService):
            created_state = "commit_unknown"

        with patch("re_gpt.multi_parent_revival.MutationService", Uncertain):
            result = revive_from_handoff(self.parents, self.artifact, "continue work", db_path=self.db)
        self.assertFalse(result["attachment_dispatched"])
        self.assertFalse(result["automatic_resend_allowed"])
        with sqlite3.connect(self.db) as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='conversation_lineage_multi_parent'").fetchone()
        self.assertIsNone(table)

    def test_ambiguous_attachment_is_not_retried(self):
        class Ambiguous(FakeMutationService):
            attached_state = "commit_unknown"

        with patch("re_gpt.multi_parent_revival.MutationService", Ambiguous):
            result = revive_from_handoff(self.parents, self.artifact, "continue work", db_path=self.db)
        self.assertEqual(result["state"], "commit_unknown")
        self.assertEqual(result["attachment_receipt"]["operation_id"], "attach-op")
        self.assertFalse(result["automatic_resend_allowed"])

    def test_rejects_duplicate_or_out_of_range_parent_sets(self):
        with self.assertRaises(ValueError):
            record_multi_parent_lineage(
                self.db, "child", ["same", "same"], handoff_path=self.artifact,
                handoff_sha256="b" * 64)
        with self.assertRaises(ValueError):
            revive_from_handoff(["only-one"], self.artifact, "continue", db_path=self.db)


if __name__ == "__main__":
    unittest.main()
