from __future__ import annotations

from pathlib import Path

from re_gpt.mutation_service import MutationService
from re_gpt.operation_store import MutationOperationStore


def _chat(prompt: str, *, assistant: str | None = None, attachment: dict | None = None):
    message = {
        "author": {"role": "user"},
        "content": {"parts": [prompt]},
        "create_time": 9999999999.0,
        "metadata": {},
    }
    if attachment is not None:
        message["metadata"]["attachments"] = [attachment]
    mapping = {"u1": {"parent": "root", "message": message}}
    if assistant is not None:
        mapping["a1"] = {
            "parent": "u1",
            "message": {
                "author": {"role": "assistant"},
                "content": {"parts": [assistant]},
                "create_time": 10000000000.0,
            },
        }
    return {"mapping": mapping}


def _browser_result():
    return {
        "resolved_thread_id": "abc",
        "transport": "dom",
        "authenticated": True,
        "hydrated": True,
        "stage_times": {"authenticated": 10.0, "hydrated": 11.0, "submission_observed": 12.0},
    }


def test_continue_returns_committed_pending_after_canonical_user_node(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(service, "_pull_and_persist", lambda conversation_id, **kwargs: {"chat": _chat("hello"), "new_messages": 1, "total_messages": 1})
    result = service.continue_thread("abc", "hello", reconcile_attempts=1)
    assert result["state"] == "committed_pending"
    assert result["user_node_id"] == "u1"
    assert result["assistant_node_id"] is None


def test_continue_only_completes_when_assistant_child_is_in_graph(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(service, "_pull_and_persist", lambda conversation_id, **kwargs: {"chat": _chat("hello", assistant="ACK"), "new_messages": 2, "total_messages": 2})
    result = service.continue_thread("abc", "hello", reconcile_attempts=1)
    assert result["state"] == "complete"
    assert result["assistant_node_id"] == "a1"
    assert result["assistant_text"] == "ACK"


def test_missing_canonical_user_node_never_retries_browser(monkeypatch, tmp_path) -> None:
    calls = []
    def fake_submit(*args, **kwargs):
        calls.append(1)
        return _browser_result()
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", fake_submit)
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(service, "_pull_and_persist", lambda conversation_id, **kwargs: {"chat": {"mapping": {}}, "new_messages": 0, "total_messages": 0})
    result = service.continue_thread("abc", "hello", reconcile_attempts=3, reconcile_interval=0)
    assert result["state"] == "commit_unknown"
    assert len(calls) == 1


def test_direct_client_rejection_falls_back_to_browser_reader(monkeypatch, tmp_path) -> None:
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    calls = []

    def sync_rejected(conversation_id):
        calls.append(("sync", conversation_id))
        raise RuntimeError("HTTP 429 Too Many Requests from direct client")

    def browser_ok(conversation_id, **kwargs):
        calls.append(("browser", conversation_id))
        return {
            "chat": _chat("hello"),
            "new_messages": 1,
            "total_messages": 1,
            "read_adapter": "browser",
            "direct_client_rejected": kwargs.get("direct_client_rejected", False),
        }

    monkeypatch.setattr(service, "_pull_sync_and_persist", sync_rejected)
    monkeypatch.setattr(service, "_pull_browser_and_persist", browser_ok)
    result = service._pull_and_persist("abc", browser_fallback=True)
    assert result["read_adapter"] == "browser"
    assert result["direct_client_rejected"] is True
    assert calls == [("sync", "abc"), ("browser", "abc")]


def test_direct_rejection_is_sticky_across_reconcile_attempts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    sync_calls = []
    browser_calls = []

    def sync_rejected(conversation_id):
        sync_calls.append(conversation_id)
        raise RuntimeError("HTTP 429 Too Many Requests from direct client")

    def browser_eventual(conversation_id, **kwargs):
        browser_calls.append((conversation_id, kwargs.get("direct_client_rejected")))
        chat = {"mapping": {}} if len(browser_calls) == 1 else _chat("hello")
        return {
            "chat": chat,
            "new_messages": 0 if len(browser_calls) == 1 else 1,
            "total_messages": 0 if len(browser_calls) == 1 else 1,
            "read_adapter": "browser",
            "read_diagnostic": {},
            "direct_client_rejected": kwargs.get("direct_client_rejected", False),
        }

    monkeypatch.setattr(service, "_pull_sync_and_persist", sync_rejected)
    monkeypatch.setattr(service, "_pull_browser_and_persist", browser_eventual)
    result = service.continue_thread("abc", "hello", reconcile_attempts=2, reconcile_interval=0)
    assert result["state"] == "committed_pending"
    assert sync_calls == ["abc"]
    assert browser_calls == [("abc", True), ("abc", True)]


def test_successful_reconcile_clears_stale_direct_rejection_error(monkeypatch, tmp_path) -> None:
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    with MutationOperationStore(service.db_path) as operations:
        op = operations.create(operation="continue", conversation_id="abc", prompt="hello")
        operation_id = str(op["operation_id"])
        operations.update(
            operation_id,
            state="commit_unknown",
            error_class="DirectClientRejected",
            error_detail="HTTP 429",
        )

    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id, **kwargs: {
            "chat": _chat("hello", assistant="ACK"),
            "new_messages": 2,
            "total_messages": 2,
            "read_adapter": "browser",
            "read_diagnostic": {},
            "direct_client_rejected": False,
        },
    )
    result = service.reconcile(operation_id, prompt="hello", not_before=0)
    assert result["state"] == "complete"
    assert result["error_class"] is None
    assert result["error_detail"] is None


def test_sync_429_after_dom_evidence_is_direct_client_rejection(monkeypatch, tmp_path) -> None:
    calls = []
    def fake_submit(*args, **kwargs):
        calls.append(1)
        return _browser_result()
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", fake_submit)
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    def rejected(_conversation_id, **kwargs):
        raise RuntimeError("HTTP 429 Too Many Requests from sync reconciliation")
    monkeypatch.setattr(service, "_pull_and_persist", rejected)
    result = service.continue_thread("abc", "hello", reconcile_attempts=1)
    assert result["state"] == "commit_unknown"
    assert result["error_class"] == "DirectClientRejected"
    assert result["completed_at"] is None
    assert len(calls) == 1


def test_follow_does_not_retry_same_direct_client_context_after_rejection(monkeypatch, tmp_path) -> None:
    pulls = []
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")

    def rejected(_conversation_id, **kwargs):
        pulls.append(1)
        raise RuntimeError("HTTP 429 Too Many Requests from sync reconciliation")

    monkeypatch.setattr(service, "_pull_and_persist", rejected)
    result = service.continue_thread(
        "abc",
        "hello",
        reconcile_attempts=3,
        reconcile_interval=0,
        follow=True,
        follow_timeout=0.01,
    )
    assert result["state"] == "commit_unknown"
    assert result["error_class"] == "DirectClientRejected"
    assert len(pulls) == 1


def test_follow_rejection_does_not_erase_prior_canonical_commit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    calls = []

    def reconcile_once_then_reject(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            with MutationOperationStore(service.db_path) as operations:
                return operations.update(
                    args[0],
                    state="committed_pending",
                    committed_at=1.0,
                    user_node_id="u1",
                )
        raise RuntimeError("HTTP 429 Too Many Requests from browser reconciliation")

    monkeypatch.setattr(service, "reconcile", reconcile_once_then_reject)
    result = service.continue_thread(
        "abc",
        "hello",
        reconcile_attempts=1,
        follow=True,
        follow_timeout=0.01,
    )
    assert result["state"] == "committed_pending"
    assert result["user_node_id"] == "u1"
    assert len(calls) == 2


def test_attachment_requires_canonical_metadata_before_complete(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "spec.md"
    file_path.write_text("spec", encoding="utf-8")
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    attachment = {"name": "spec.md", "mime_type": "text/markdown", "size": 4, "id": "file_123"}
    monkeypatch.setattr(service, "_pull_and_persist", lambda conversation_id, **kwargs: {"chat": _chat("hello", assistant="ACK", attachment=attachment), "new_messages": 2, "total_messages": 2})
    result = service.continue_thread("abc", "hello", attachments=[file_path], reconcile_attempts=1)
    assert result["state"] == "complete"
    assert result["attachment_ids"] == ["file_123"]
    status = service.operation_status(result["operation_id"])
    assert status["attachments"][0]["canonical_file_id"] == "file_123"
