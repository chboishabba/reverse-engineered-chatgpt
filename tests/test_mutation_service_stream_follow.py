from __future__ import annotations

from re_gpt import mutation_service as ms


def test_follow_reconciles_once_after_terminal_stream(monkeypatch, tmp_path) -> None:
    submission = {
        "conversation_id": "conv-1",
        "resolved_thread_id": "conv-1",
        "transport": "async",
        "state": "submission_observed",
        "user_message_id": "user-1",
        "assistant_message_id": "assistant-1",
        "stream_terminal_observed": True,
        "stream_terminal_reason": "done",
        "stream_event_counts": {"token_delta": 2, "terminal": 1},
        "stage_times": {"authenticated": 1.0, "hydrated": 2.0, "submission_observed": 3.0},
        "protocol_observations": [],
    }
    monkeypatch.setattr(ms, "submit_async_prompt_once", lambda *args, **kwargs: submission)
    service = ms.MutationService(db_path=tmp_path / "state.db")
    calls = []

    def reconcile(operation_id, **kwargs):
        calls.append((operation_id, kwargs))
        return {
            "operation_id": operation_id,
            "state": "complete",
            "conversation_id": "conv-1",
            "assistant_text": "ok",
            "rejected_read_adapters": [],
        }

    monkeypatch.setattr(service, "reconcile", reconcile)
    result = service.continue_thread(
        "conv-1",
        "hello",
        write_transport="async",
        dom_fallback=False,
        follow=True,
        reconcile_attempts=9,
        reconcile_interval=0.0,
    )
    assert result["state"] == "complete"
    assert result["stream_terminal_observed"] is True
    assert len(calls) == 1


def test_follow_progress_callback_is_forwarded_to_async_stream(monkeypatch, tmp_path) -> None:
    seen = {}

    def submit(*args, **kwargs):
        seen["callback"] = kwargs.get("progress_callback")
        callback = seen["callback"]
        if callback:
            callback({"kind": "token_delta", "delta": "hi"})
        return {
            "conversation_id": "conv-1",
            "resolved_thread_id": "conv-1",
            "transport": "async",
            "state": "submission_observed",
            "stream_terminal_observed": True,
            "stream_terminal_reason": "done",
            "stream_event_counts": {"token_delta": 1, "terminal": 1},
            "stage_times": {},
            "protocol_observations": [],
        }

    monkeypatch.setattr(ms, "submit_async_prompt_once", submit)
    service = ms.MutationService(db_path=tmp_path / "state.db")
    monkeypatch.setattr(service, "reconcile", lambda operation_id, **kwargs: {"operation_id": operation_id, "state": "complete"})
    progress = []
    service.continue_thread(
        "conv-1",
        "hello",
        write_transport="async",
        dom_fallback=False,
        follow=True,
        progress_callback=progress.append,
    )
    assert seen["callback"] is progress.append or callable(seen["callback"])
    assert progress == [{"kind": "token_delta", "delta": "hi"}]
