from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re_gpt.multi_parent_revival import record_multi_parent_lineage, revive_from_handoff


class FakeMutationService:
    created_state = "complete"
    attached_state = "complete"
    new_count = 0
    continue_count = 0

    def __init__(self, **kwargs):
        pass

    def new_conversation(self, prompt, **kwargs):
        type(self).new_count += 1
        return {"state": self.created_state, "conversation_id": "child-1",
                "operation_id": "create-op"}

    def continue_thread(self, conversation_id, prompt, **kwargs):
        type(self).continue_count += 1
        return {"state": self.attached_state, "operation_id": "attach-op",
                "attachment_ids": ["file-1"], "canonical_attachment_count": 1,
                "user_node_id": "user-node-1"}


def handoff(parents: list[str]) -> dict:
    projections = [{
        "canonical_thread_id": parent,
        "packet_id": f"packet-{parent}",
        "source_refs": [],
        "span_receipt": {"full_thread_scanned": True},
        "semantic_pnf_execution": {
            "status": "typed_pnf_not_compiled",
            "candidate_only": True,
            "no_promotion": True,
        },
    } for parent in parents]
    return {
        "version": "itir.pnf.multi_parent_handoff.v1",
        "child_thread_id": None,
        "parent_count": len(parents),
        "parents": projections,
        "candidate_world": {
            "status": "candidate_only",
            "nodes": [],
            "cross_parent_equivalences": [],
            "cross_parent_contradictions": [],
            "reviewed_world_model": False,
            "formal_proof": False,
        },
        "materialization": {
            "artifact_content": "canonical_source_references_and_lexical_candidate_spans_only",
            "raw_transcript_included": False,
            "typed_world_model_included": False,
            "reviewed_claims_included": False,
            "proof_included": False,
            "source_content_materialization": "not_authorized_by_read_only_projection",
        },
        "lineage_receipt": {
            "relationship": "multi_parent_proposed",
            "parents": [{"canonical_thread_id": parent} for parent in parents],
            "child_thread_id": None,
            "parents_remain_independent_authorities": True,
            "parent_retirement": False,
            "durability": "handoff_receipt_only_until_child_attachment_receipt",
        },
        "governance": {
            "dispatch_allowed": False,
            "automatic_resend_allowed": False,
            "browser_dom_allowed": False,
        },
        "authority": {
            "canonical_archive": "corpus_authority_read_only",
            "transport_receipts": "bounded_operation_evidence_only",
            "candidate_world": "not_reviewed_not_promoted",
            "equivalence": "not_established",
            "formal_proof": "not_established",
            "dispatch": False,
            "automatic_resend": False,
        },
        "formal_gap": {
            "status": "open",
            "selected_context_sufficiency_proved": False,
        },
        "packet_id": "packet-root",
    }


class MultiParentRevivalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "operations.sqlite"
        self.artifact = Path(self.temp.name) / "handoff.json"
        self.parents = ["canonical-p1", "canonical-p2"]
        self.artifact.write_text(json.dumps(handoff(self.parents)))
        FakeMutationService.new_count = 0
        FakeMutationService.continue_count = 0

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
            result = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="req-1", db_path=self.db)
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
            result = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="req-uncertain", db_path=self.db)
            replay = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="req-uncertain", db_path=self.db)
        self.assertFalse(result["attachment_dispatched"])
        self.assertFalse(result["automatic_resend_allowed"])
        self.assertTrue(replay["replayed_request"])
        self.assertEqual(Uncertain.new_count, 1)
        with sqlite3.connect(self.db) as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='conversation_lineage_multi_parent'").fetchone()
        self.assertIsNone(table)

    def test_ambiguous_attachment_is_not_retried(self):
        class Ambiguous(FakeMutationService):
            attached_state = "commit_unknown"

        with patch("re_gpt.multi_parent_revival.MutationService", Ambiguous):
            result = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="req-ambiguous", db_path=self.db)
            replay = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="req-ambiguous", db_path=self.db)
        self.assertEqual(result["state"], "commit_unknown")
        self.assertEqual(result["attachment_receipt"]["operation_id"], "attach-op")
        self.assertFalse(result["automatic_resend_allowed"])
        self.assertTrue(replay["replayed_request"])
        self.assertEqual(Ambiguous.continue_count, 1)

    def test_repeated_request_id_never_dispatches_twice(self):
        with patch("re_gpt.multi_parent_revival.MutationService", FakeMutationService):
            first = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="stable-req", db_path=self.db)
            second = revive_from_handoff(
                self.parents, self.artifact, "continue work",
                request_id="stable-req", db_path=self.db)
        self.assertFalse(first["replayed_request"])
        self.assertTrue(second["replayed_request"])
        self.assertFalse(second["attachment_dispatched"])
        self.assertEqual(FakeMutationService.new_count, 1)
        self.assertEqual(FakeMutationService.continue_count, 1)

    def test_rejects_inconsistent_lineage_before_any_write(self):
        unsafe = handoff(self.parents)
        unsafe["lineage_receipt"]["parents_remain_independent_authorities"] = False
        self.artifact.write_text(json.dumps(unsafe))
        with patch("re_gpt.multi_parent_revival.MutationService", FakeMutationService):
            with self.assertRaisesRegex(ValueError, "independent parent authorities"):
                revive_from_handoff(
                    self.parents, self.artifact, "continue work",
                    request_id="unsafe-lineage", db_path=self.db)
        self.assertEqual(FakeMutationService.new_count, 0)
        self.assertEqual(FakeMutationService.continue_count, 0)

    def test_rejects_transcript_materialization_before_any_write(self):
        unsafe = handoff(self.parents)
        unsafe["materialization"]["raw_transcript_included"] = True
        self.artifact.write_text(json.dumps(unsafe))
        with patch("re_gpt.multi_parent_revival.MutationService", FakeMutationService):
            with self.assertRaisesRegex(ValueError, "materialization"):
                revive_from_handoff(
                    self.parents, self.artifact, "continue work",
                    request_id="unsafe-materialization", db_path=self.db)
        self.assertEqual(FakeMutationService.new_count, 0)

    def test_mcp_entrypoint_requires_and_forwards_request_id(self):
        from re_gpt.mcp_server import chatgpt_revive_multi

        with patch(
            "re_gpt.multi_parent_revival.revive_from_handoff",
            return_value={"state": "accepted"},
        ) as revive:
            result = chatgpt_revive_multi(
                self.parents, str(self.artifact), "continue work",
                request_id="stable-mcp-request", db_path=str(self.db), follow=False,
            )
        self.assertEqual(result, {"state": "accepted"})
        self.assertEqual(revive.call_args.kwargs["request_id"], "stable-mcp-request")

    def test_rejects_scalar_parent_container_and_nonstring_request_id(self):
        with patch("re_gpt.multi_parent_revival.MutationService", FakeMutationService):
            with self.assertRaisesRegex(ValueError, "sequence of canonical string IDs"):
                revive_from_handoff(
                    "ab", self.artifact, "continue work",
                    request_id="scalar-parents", db_path=self.db)
            with self.assertRaisesRegex(ValueError, "request_id must be a caller-supplied string"):
                revive_from_handoff(
                    self.parents, self.artifact, "continue work",
                    request_id=123, db_path=self.db)
        self.assertEqual(FakeMutationService.new_count, 0)
        self.assertEqual(FakeMutationService.continue_count, 0)

    def test_rejects_duplicate_or_out_of_range_parent_sets(self):
        with self.assertRaises(ValueError):
            record_multi_parent_lineage(
                self.db, "child", ["same", "same"], handoff_path=self.artifact,
                handoff_sha256="b" * 64)
        with self.assertRaises(ValueError):
            revive_from_handoff(
                ["only-one"], self.artifact, "continue",
                request_id="req-bad", db_path=self.db)
        with self.assertRaises(ValueError):
            revive_from_handoff(
                ["canonical-p1", "canonical-p1"], self.artifact, "continue",
                request_id="req-duplicate", db_path=self.db)


if __name__ == "__main__":
    unittest.main()
