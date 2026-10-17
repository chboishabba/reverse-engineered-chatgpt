from __future__ import annotations

from pathlib import Path

from re_gpt.graph_broker import GraphAcquisitionFailed, GraphAcquisitionResult
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


def _broker_result(chat: dict, *, adapter: str = "async", rejected: tuple[str, ...] = ()):
    attempts = tuple(
        {"adapter": name, "status": 429, "response_class": "RuntimeError", "rejected": True, "elapsed_ms": 1.0}
        for name in rejected
    ) + (
        {"adapter": adapter, "status": 200, "response_class": "ok", "rejected": False, "elapsed_ms": 2.0},
    )
    return GraphAcquisitionResult(
        graph=chat,
        adapter=adapter,
        elapsed_ms=2.0,
        diagnostic={"adapter": adapter, "status": 200},
        attempts=attempts,
        attempted_adapters=tuple(item["adapter"] for item in attempts),
        rejected_adapters=rejected,
    )


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


def test_broker_result_is_persisted_through_shared_path(monkeypatch, tmp_path) -> None:
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    broker = _broker_result(_chat("hello"), adapter="async")
    monkeypatch.setattr("re_gpt.mutation_service.fetch_exact_graph", lambda *args, **kwargs: broker)

    result = service._pull_and_persist("abc")

    assert result["read_adapter"] == "async"
    assert result["attempted_read_adapters"] == ["async"]
    assert result["rejected_read_adapters"] == []
    assert result["chat"]["mapping"]["u1"]["message"]["content"]["parts"] == ["hello"]


def test_broker_rejections_are_exposed_on_success(monkeypatch, tmp_path) -> None:
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    broker = _broker_result(_chat("hello"), adapter="browser", rejected=("async", "sync"))
    monkeypatch.setattr("re_gpt.mutation_service.fetch_exact_graph", lambda *args, **kwargs: broker)

    result = service._pull_and_persist("abc")

    assert result["read_adapter"] == "browser"
    assert result["attempted_read_adapters"] == ["async", "sync", "browser"]
    assert result["rejected_read_adapters"] == ["async", "sync"]


def test_all_graph_adapters_failing_after_send_never_resends(monkeypatch, tmp_path) -> None:
    sends = []
    monkeypatch.setattr("re_gpt.mutation_service.submit_prompt", lambda *args, **kwargs: sends.append(1) or _browser_result())
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    failure = GraphAcquisitionFailed((
        {"adapter": "async", "status": 429, "response_class": "RuntimeError", "rejected": True, "elapsed_ms": 1.0},
        {"adapter": "sync", "status": 429, "response_class": "RuntimeError", "rejected": True, "elapsed_ms": 1.0},
        {"adapter": "browser", "status": 429, "response_class": "RuntimeError", "rejected": True, "elapsed_ms": 1.0},
    ))
    monkeypatch.setattr(service, "_pull_and_persist", lambda *args, **kwargs: (_ for _ in ()).throw(failure))

    result = service.continue_thread("abc", "hello", reconcile_attempts=3, reconcile_interval=0)

    assert result["state"] == "commit_unknown"
    assert result["error_class"] == "GraphAcquisitionFailed"
    assert sends == [1]


def test_successful_reconcile_clears_stale_read_error(monkeypatch, tmp_path) -> None:
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    with MutationOperationStore(service.db_path) as operations:
        op = operations.create(operation="continue", conversation_id="abc", prompt="hello")
        operation_id = str(op["operation_id"])
        operations.update(
            operation_id,
            state="commit_unknown",
            error_class="GraphAcquisitionFailed",
            error_detail="all adapters rejected",
        )

    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id, **kwargs: {
            "chat": _chat("hello", assistant="ACK"),
            "new_messages": 2,
            "total_messages": 2,
            "read_adapter": "async",
            "read_diagnostic": {},
            "attempted_read_adapters": ["async"],
            "rejected_read_adapters": [],
        },
    )
    result = service.reconcile(operation_id, prompt="hello", not_before=0)
    assert result["state"] == "complete"
    assert result["error_class"] is None
    assert result["error_detail"] is None


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
        raise GraphAcquisitionFailed((
            {"adapter": "async", "status": 429, "response_class": "RuntimeError", "rejected": True, "elapsed_ms": 1.0},
        ))

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
