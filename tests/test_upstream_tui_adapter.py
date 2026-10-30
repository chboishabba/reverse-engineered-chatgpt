"""Contract tests for tritao TUI callback adaptation; offline and no DOM."""
import unittest
from unittest.mock import patch

from re_gpt.upstream_tui_adapter import ReGptTuiCallbacks, project_chat


class TestUpstreamTuiAdapter(unittest.TestCase):
    def test_project_read_preserves_source(self):
        graph = {"title": "Math", "mapping": {
            "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["$x^2$"]}}},
            "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": [r"\\[a=b\\]"]}}},
        }}
        converted = project_chat("cid", graph)
        self.assertEqual(converted["title"], "Math")
        self.assertEqual([x["text"] for x in converted["messages"]], ["$x^2$", r"\\[a=b\\]"])

    def test_upload_rejected_before_any_mutation(self):
        callbacks = ReGptTuiCallbacks()
        with patch.object(callbacks.service, "continue_thread") as mutation:
            with self.assertRaises(NotImplementedError):
                callbacks.send_prompt("cid", "hello", [{"image": "png"}], lambda _: None, lambda _: None)
            mutation.assert_not_called()

    def test_unknown_operation_never_resends(self):
        callbacks = ReGptTuiCallbacks()
        with patch.object(callbacks.service, "continue_thread",
                          return_value={"state": "commit_unknown", "operation_id": "op-1"} ) as mutation:
            with self.assertRaisesRegex(RuntimeError, "do not resend"):
                callbacks.send_prompt("cid", "hello", [], lambda _: None, lambda _: None)
            self.assertEqual(mutation.call_count, 1)

    def test_completed_mutation_emits_final_and_reads_canonical(self):
        callbacks = ReGptTuiCallbacks()
        events = []
        with patch.object(callbacks.service, "continue_thread", return_value={
            "state": "complete", "conversation_id": "cid", "assistant_text": "$x$"
        }) as mutation, patch.object(callbacks, "load_conversation", return_value={
            "conversation_id": "cid", "title": "T", "messages": []
        }):
            result = callbacks.send_prompt("cid", "hello", [], events.append, lambda _: None)
        self.assertEqual(result["conversation_id"], "cid")
        self.assertEqual(events[-1], {"type": "replace", "text": "$x$"})
        self.assertFalse(mutation.call_args.kwargs["dom_fallback"])
        self.assertEqual(mutation.call_args.kwargs["write_transport"], "async")


if __name__ == "__main__":
    unittest.main()
