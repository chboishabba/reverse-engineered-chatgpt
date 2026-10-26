from __future__ import annotations

from pathlib import Path

import pytest

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


def test_continue_thread_async_write_transport_certifies_via_broker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_async_prompt_once",
        lambda *args, **kwargs: {
            "resolved_thread_id": "abc",
            "transport": "async",
            "state": "submission_observed",
            "protocol_observations": [{"transport": "async", "operation": "continue", "status": 200}],
            "stage_times": {"authenticated": 10.0, "hydrated": 11.0, "submission_observed": 12.0},
        },
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")
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
            "read_observations": [{"transport": "async", "operation": "exact_read", "status": 200}],
        },
    )
    result = service.continue_thread("abc", "hello", write_transport="async", reconcile_attempts=1)
    assert result["state"] == "complete"
    assert result["transport"] == "async"
    assert result["read_adapter"] == "async"
    assert result["read_observations"] == [{"transport": "async", "operation": "exact_read", "status": 200}]
    assert result["write_observations"] == [{"transport": "async", "operation": "continue", "status": 200}]


def test_continue_thread_ambiguous_async_write_never_falls_back_to_dom(monkeypatch, tmp_path: Path) -> None:
    from re_gpt.async_mutation_probe import AmbiguousAsyncWriteError
    from re_gpt.protocol_observation import build_protocol_observation

    dom_calls: list[int] = []
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_prompt",
        lambda *args, **kwargs: dom_calls.append(1) or _browser_result(),
    )
    obs = build_protocol_observation(
        transport="async",
        operation="continue",
        method="POST",
        path="/backend-api/conversation",
        status=None,
        response_class="TimeoutError",
    )
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_async_prompt_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AmbiguousAsyncWriteError("ambiguous post", status=None, observations=(obs,))
        ),
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    result = service.continue_thread("abc", "hello", write_transport="async", reconcile_attempts=1)
    assert result["state"] == "commit_unknown"
    assert result["transport"] == "async"
    assert result["error_class"] == "AmbiguousAsyncWriteError"
    assert len(result["write_observations"]) == 1
    assert dom_calls == []


def test_continue_thread_async_attachment_certifies_canonical_file_id(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "spec.md"
    file_path.write_text("spec", encoding="utf-8")
    captured_kwargs: dict = {}

    def fake_async_submit(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {
            "resolved_thread_id": "abc",
            "transport": "async",
            "state": "submission_observed",
            "attachment_ids": ["file_123"],
            "protocol_observations": [
                {"transport": "async", "operation": "continue", "bootstrap_stage": "attachment_staging", "status": 200},
                {"transport": "async", "operation": "continue", "bootstrap_stage": "conversation_post", "status": 200},
            ],
            "stage_times": {
                "authenticated": 10.0,
                "hydrated": 11.0,
                "attachment_ready": 11.5,
                "submission_observed": 12.0,
            },
        }

    monkeypatch.setattr("re_gpt.mutation_service.submit_async_prompt_once", fake_async_submit)
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    attachment = {"name": "spec.md", "mime_type": "text/markdown", "size": 4, "id": "file_123"}
    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id, **kwargs: {
            "chat": _chat("hello", assistant="ACK", attachment=attachment),
            "new_messages": 2,
            "total_messages": 2,
        },
    )
    result = service.continue_thread(
        "abc",
        "hello",
        attachments=[file_path],
        write_transport="async",
        reconcile_attempts=1,
    )
    assert len(captured_kwargs["attachments"]) == 1
    assert captured_kwargs["attachments"][0].filename == "spec.md"
    assert result["state"] == "complete"
    assert result["transport"] == "async"
    assert result["attachment_ids"] == ["file_123"]
    status = service.operation_status(result["operation_id"])
    assert status["attachment_ready_at"] == 11.5
    assert status["attachments"][0]["canonical_file_id"] == "file_123"


def test_continue_thread_dom_fallback_recovers_from_explicit_precommit_rejection_only(monkeypatch, tmp_path: Path) -> None:
    from re_gpt.async_mutation_probe import ExplicitAsyncWriteRejection
    from re_gpt.protocol_observation import build_protocol_observation

    obs = build_protocol_observation(
        transport="async",
        operation="continue",
        method="POST",
        path="/backend-api/conversation",
        status=403,
        response_class="http_403",
        bootstrap_stage="conversation_post",
    )
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_async_prompt_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExplicitAsyncWriteRejection("rejected 403", stage="conversation_post", status=403, observations=(obs,))
        ),
    )
    dom_calls: list[int] = []
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_prompt",
        lambda *args, **kwargs: dom_calls.append(1) or _browser_result(),
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(
        service,
        "_pull_and_persist",
        lambda conversation_id, **kwargs: {
            "chat": _chat("hello", assistant="ACK"),
            "new_messages": 2,
            "total_messages": 2,
        },
    )
    result = service.continue_thread(
        "abc",
        "hello",
        write_transport="async",
        dom_fallback=True,
        reconcile_attempts=1,
    )
    assert dom_calls == [1]
    assert result["state"] == "complete"
    assert result["transport"] == "dom"
    assert len(result["write_observations"]) == 1
    assert result["write_observations"][0]["status"] == 403


def test_async_precommit_failure_persists_redacted_observation(monkeypatch, tmp_path: Path) -> None:
    from re_gpt.async_mutation_probe import ExplicitAsyncWriteRejection
    from re_gpt.protocol_observation import build_protocol_observation

    obs = build_protocol_observation(
        transport="async",
        operation="continue",
        method="GET",
        path="/backend-api/conversation/conv-1",
        status=401,
        response_class="UnexpectedResponseError",
        bootstrap_stage="conversation_get",
        headers={"Authorization": "Bearer SECRET", "Oai-device-id": "SECRET_DEVICE"},
    )
    monkeypatch.setattr(
        "re_gpt.mutation_service.submit_async_prompt_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExplicitAsyncWriteRejection("preflight rejected", stage="conversation_get", status=401, observations=(obs,))
        ),
    )
    service = MutationService(db_path=tmp_path / "history.sqlite3")

    with pytest.raises(ExplicitAsyncWriteRejection):
        service.continue_thread("conv-1", "hello", write_transport="async", dom_fallback=False)

    with MutationOperationStore(service.db_path) as operations:
        operation_id = str(operations.recent(1)[0]["operation_id"])
    status = service.operation_status(operation_id)
    assert status is not None
    assert status["state"] == "failed_precommit"
    assert '"status":401' in status["error_detail"]
    assert '"stage":"conversation_get"' in status["error_detail"]
    assert "SECRET" not in status["error_detail"]


def test_mcp_chatgpt_continue_defaults_to_async_without_dom_fallback(monkeypatch, tmp_path: Path) -> None:
    from re_gpt import mcp_server

    called: dict = {}

    class DummyService:
        def continue_thread(self, conversation_id: str, prompt: str, **kwargs):
            called["conversation_id"] = conversation_id
            called["prompt"] = prompt
            called.update(kwargs)
            return {"state": "complete", "transport": "async"}

    monkeypatch.setattr(mcp_server, "_service", lambda db_path=None: DummyService())
    res = mcp_server.chatgpt_continue("conv-1", "hello", attachments=["a.txt"])
    assert res["state"] == "complete"
    assert called["write_transport"] == "async"
    assert called["dom_fallback"] is False
    assert list(called["attachments"]) == ["a.txt"]

