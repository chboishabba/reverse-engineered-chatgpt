"""Offline source-level acceptance for server jobs and PG event routing."""
from unittest.mock import patch, Mock
from re_gpt.pg_turn_worker import process_one
from re_gpt.pg_conversation_subscriptions import PGConversationSubscriber
from re_gpt.terminal_math_layout import unicode_math
from re_gpt.terminal_rich_display import terminal_display


def test_worker_streams_with_stable_source_ids():
    sent = []
    def make_service():
        service = Mock()
        def continue_thread(cid, prompt, **options):
            options["progress_callback"]({"kind": "token_delta", "delta": "A"})
            options["progress_callback"]({"kind": "tool_result", "body": "observed"})
            return {"state": "complete", "operation_id": "remote-123",
                    "conversation_id": cid}
        service.continue_thread.side_effect = continue_thread
        return service
    with patch("re_gpt.pg_turn_worker.claim_next", return_value={
            "job_id": "job-A", "conversation_id": "thread-A", "prompt": "test",
            "actor": "human"}), patch(
            "re_gpt.pg_turn_worker.append_event",
            side_effect=lambda *args, **kwargs: sent.append(args)), patch(
            "re_gpt.pg_turn_worker.finish") as finish, patch(
            "re_gpt.pg_hydration.hydrate_one", return_value={"state": "hydrated"}):
        receipt = process_one(service_factory=make_service)
    assert receipt["state"] == "complete"
    assert [event[2] for event in sent] == [1, 2, 3]
    assert all(event[0] == "thread-A" and event[1] == "job-A" for event in sent)
    finish.assert_called_once()


def test_worker_exception_never_resends():
    service = Mock()
    service.continue_thread.side_effect = RuntimeError("unknown commit")
    with patch("re_gpt.pg_turn_worker.claim_next", return_value={
            "job_id": "job-B", "conversation_id": "thread-B", "prompt": "test",
            "actor": "human"}), patch(
            "re_gpt.pg_turn_worker.append_event"), patch(
            "re_gpt.pg_turn_worker.finish") as finish:
        result = process_one(service_factory=lambda: service)
    assert result["state"] == "commit_unknown"
    assert service.continue_thread.call_count == 1
    assert finish.call_args.args[1] == "commit_unknown"


def test_math_rendering_keeps_code_fences_and_formats_matrices():
    math = unicode_math(r"\begin{bmatrix}a & b \\ c & d\end{bmatrix}")
    assert "a" in math and "b" in math and "\n" in math
    assert "⎡" in math and "⎦" in math
    assert "(a)/(b)" in unicode_math(r"\frac{a}{b}")
    src = r"\[\begin{bmatrix}a & b \\ c & d\end{bmatrix}\]"
    assert "⎡" in terminal_display(src)
    fence = chr(96)*3
    fenced = fence + "\n" + src + "\n" + fence
    assert terminal_display(fenced) == fenced


def test_subscription_is_source_agnostic():
    calls = []
    subscriber = PGConversationSubscriber(
        revision_reader=lambda cid: "rev-" + cid,
        event_reader=lambda cid, **kw: {"events": [
            {"operation_id": cid + "-job", "kind": "tool_result"}],
            "next_cursor": kw["after"] + 1})
    subscriber._callbacks = {"thread-A": lambda r: calls.append(r)}
    counter = iter([False, True])
    subscriber._stop.wait = lambda interval: next(counter)
    subscriber._run()
    assert calls[0]["conversation_id"] == "thread-A"
    assert calls[0]["events"][0]["operation_id"] == "thread-A-job"
