from __future__ import annotations

from re_gpt.mutation_service import MutationService


def _chat(prompt: str, *, assistant: str | None = None):
    mapping = {
        "u1": {
            "parent": "root",
            "message": {
                "author": {"role": "user"},
                "content": {"parts": [prompt]},
                "create_time": 100.0,
            },
        }
    }
    if assistant is not None:
        mapping["a1"] = {
            "parent": "u1",
            "message": {
                "author": {"role": "assistant"},
                "content": {"parts": [assistant]},
                "create_time": 101.0,
            },
        }
    return {"mapping": mapping}


def test_continue_returns_committed_pending_after_canonical_user_node(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_prompt",
        lambda *args, **kwargs: {
            "resolved_thread_id": "abc",
            "transport": "dom",
            "authenticated": True,
            "hydrated": True,
            "stage_times": {"authenticated": 10.0, "hydrated": 11.0, "submission_observed": 12.0},
        },
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id: {"chat": _chat("hello"), "new_messages": 1, "total_messages": 1},
    )

    result = service.continue_thread("abc", "hello", reconcile_attempts=1)
    assert result["state"] == "committed_pending"
    assert result["user_node_id"] == "u1"
    assert result["assistant_node_id"] is None


def test_continue_only_completes_when_assistant_child_is_in_graph(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_prompt",
        lambda *args, **kwargs: {"resolved_thread_id": "abc", "authenticated": True, "hydrated": True},
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id: {"chat": _chat("hello", assistant="ACK"), "new_messages": 2, "total_messages": 2},
    )

    result = service.continue_thread("abc", "hello", reconcile_attempts=1)
    assert result["state"] == "complete"
    assert result["assistant_node_id"] == "a1"
    assert result["assistant_text"] == "ACK"


def test_missing_canonical_user_node_never_retries_browser(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_submit(*args, **kwargs):
        calls.append(1)
        return {"resolved_thread_id": "abc", "authenticated": True, "hydrated": True}

    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", fake_submit)
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id: {"chat": {"mapping": {}}, "new_messages": 0, "total_messages": 0},
    )

    result = service.continue_thread(
        "abc",
        "hello",
        reconcile_attempts=3,
        reconcile_interval=0,
    )
    assert result["state"] == "commit_unknown"
    assert len(calls) == 1
