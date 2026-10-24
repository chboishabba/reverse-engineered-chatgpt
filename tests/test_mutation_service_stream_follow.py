from __future__ import annotations

import json

import pytest

from re_gpt import mutation_service as ms
from re_gpt.async_mutation_probe import AmbiguousAsyncWriteError


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

@pytest.mark.parametrize(
    ("terminal", "graph_result", "expected_state", "reads"),
    [
        (True, {"state": "complete", "user_node_id": "user-1"}, "complete", 1),
        (True, RuntimeError("SECRET_GRAPH_BODY"), "commit_unknown", 1),
        (True, {"state": "commit_unknown"}, "commit_unknown", 1),
        (False, {"state": "complete", "user_node_id": "user-1"}, "commit_unknown", 0),
        (True, {"state": "complete", "user_node_id": "other-user"}, "commit_unknown", 1),
    ],
)
def test_timeout_reconciliation_is_direct_once_and_fail_closed(
    monkeypatch, tmp_path, terminal, graph_result, expected_state, reads,
) -> None:
    post_calls = []
    graph_calls = []
    dom_calls = []
    def submit(*args, **kwargs):
        post_calls.append((args, kwargs))
        assert kwargs["allow_browser_bootstrap"] is False
        raise AmbiguousAsyncWriteError(
            "post timeout",
            transport_timeout=True,
            expected_user_message_id="user-1",
            stream_evidence={
                "stream_terminal_observed": terminal,
                "stream_terminal_reason": "done" if terminal else None,
                "stream_event_counts": {"terminal": int(terminal)},
            },
        )
    def reconcile(operation_id, **kwargs):
        graph_calls.append(kwargs)
        assert kwargs["browser_fallback"] is False
        assert "browser" in kwargs["skip_read_adapters"]
        if isinstance(graph_result, Exception):
            raise graph_result
        return {"operation_id": operation_id, **graph_result}
    monkeypatch.setattr(ms, "submit_async_prompt_once", submit)
    monkeypatch.setattr(ms, "submit_prompt", lambda *a, **k: dom_calls.append(True))
    service = ms.MutationService(db_path=tmp_path / "state.db")
    monkeypatch.setattr(service, "reconcile", reconcile)
    result = service.continue_thread(
        "conv-1", "SECRET_PROMPT_BODY", write_transport="async",
        dom_fallback=True, follow=True, reconcile_attempts=8,
    )
    assert result["state"] == expected_state
    assert len(post_calls) == 1
    assert len(graph_calls) == reads
    assert dom_calls == []
    diagnostic = json.dumps({
        "stream_evidence": result.get("stream_evidence"),
        "error_detail": result.get("error_detail"),
    })
    assert "SECRET_PROMPT_BODY" not in diagnostic
    assert "SECRET_GRAPH_BODY" not in diagnostic


def test_non_timeout_post_ambiguity_never_reconciles_or_falls_back(monkeypatch, tmp_path) -> None:
    calls = {"post": 0, "read": 0, "dom": 0}
    def submit(*args, **kwargs):
        calls["post"] += 1
        raise AmbiguousAsyncWriteError("ambiguous post", stream_evidence={
            "stream_terminal_observed": True,
        })
    monkeypatch.setattr(ms, "submit_async_prompt_once", submit)
    monkeypatch.setattr(ms, "submit_prompt", lambda *a, **k: calls.__setitem__("dom", calls["dom"] + 1))
    service = ms.MutationService(db_path=tmp_path / "state.db")
    monkeypatch.setattr(service, "reconcile", lambda *a, **k: calls.__setitem__("read", calls["read"] + 1))
    result = service.continue_thread("conv-1", "hello", write_transport="async", dom_fallback=True)
    assert result["state"] == "commit_unknown"
    assert calls == {"post": 1, "read": 0, "dom": 0}
