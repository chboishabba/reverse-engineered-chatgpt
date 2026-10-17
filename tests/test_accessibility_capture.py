from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from re_gpt.accessibility_capture import CaptureDependencies, capture_attachment_continue


def _before_graph() -> dict:
    return {
        "id": "conversation-1",
        "current_node": "user-old",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user-old"], "message": None},
            "user-old": {
                "id": "user-old",
                "parent": "root",
                "children": [],
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["old"]},
                    "status": "finished_successfully",
                    "end_turn": True,
                    "create_time": 1.0,
                    "metadata": {},
                },
            },
        },
    }


def _after_graph() -> dict:
    graph = _before_graph()
    graph["current_node"] = "assistant-new"
    graph["mapping"]["user-old"]["children"] = ["user-new"]
    graph["mapping"]["user-new"] = {
        "id": "user-new",
        "parent": "user-old",
        "children": ["assistant-new"],
        "message": {
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["probe"]},
            "status": "finished_successfully",
            "end_turn": True,
            "create_time": 2.0,
            "metadata": {
                "attachments": [
                    {
                        "id": "file-xyz",
                        "name": "probe.txt",
                        "mime_type": "text/plain",
                        "size": 6,
                        "asset_pointer": "sediment://file-xyz",
                    }
                ]
            },
        },
    }
    graph["mapping"]["assistant-new"] = {
        "id": "assistant-new",
        "parent": "user-new",
        "children": [],
        "message": {
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["done"]},
            "status": "finished_successfully",
            "end_turn": True,
            "create_time": 3.0,
            "metadata": {},
        },
    }
    return graph


def _graph_result(graph: dict, adapter: str = "async") -> SimpleNamespace:
    return SimpleNamespace(
        graph=graph,
        adapter=adapter,
        diagnostic={},
        attempted_adapters=(adapter,),
        rejected_adapters=(),
        observations=(),
    )


def test_capture_attachment_continue_actuates_once_and_builds_canonical_receipt(tmp_path: Path) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    graph_reads = iter([_graph_result(_before_graph()), _graph_result(_after_graph())])
    calls = {"actuator": 0, "reconcile": 0}

    def graph_reader(*args, **kwargs):
        return next(graph_reads)

    def actuator(*args, **kwargs):
        calls["actuator"] += 1
        return {
            "resolved_thread_id": "conversation-1",
            "protocol_observations": [
                {
                    "sequence_index": 1,
                    "bootstrap_stage": "attachment_staging",
                    "method": "POST",
                    "path": "/backend-api/files",
                    "status": 201,
                    "response_class": "json_ok",
                },
                {
                    "sequence_index": 2,
                    "bootstrap_stage": "conversation_post",
                    "method": "POST",
                    "path": "/backend-api/conversation",
                    "status": 200,
                    "response_class": "sse_stream",
                },
            ],
            "stage_times": {"submission_observed": 20.0},
            "authenticated": True,
            "hydrated": True,
        }

    def reconciler(**kwargs):
        calls["reconcile"] += 1
        return {
            "state": "complete",
            "user_node_id": "user-new",
            "assistant_node_id": "assistant-new",
            "attachment_ids": ["file-xyz"],
            "read_adapter": "async",
            "attempted_read_adapters": ["async"],
            "rejected_read_adapters": [],
            "read_observations": [],
        }

    receipt = capture_attachment_continue(
        "conversation-1",
        "ACCESSIBILITY-PROBE",
        [attachment],
        db_path=tmp_path / "capture.db",
        dependencies=CaptureDependencies(
            graph_reader=graph_reader,
            actuator=actuator,
            reconciler=reconciler,
        ),
    )

    assert calls == {"actuator": 1, "reconcile": 1}
    assert receipt["actuator_attempts"] == 1
    assert receipt["canonical_receipt"]["state"] == "complete"
    assert receipt["canonical_receipt"]["user_node_id"] == "user-new"
    assert receipt["canonical_receipt"]["assistant_node_id"] == "assistant-new"
    assert receipt["canonical_receipt"]["attachment_ids"] == ["file-xyz"]
    assert receipt["graph_delta"]["added_nodes"] == ["assistant-new", "user-new"]
    assert receipt["local_inputs"]["attachments"][0]["sha256"]
    assert receipt["protocol_stages"][0]["stage"] == "attachment_staging"


def test_actuator_exception_never_retries_and_still_reconciles(tmp_path: Path) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    graph_reads = iter([_graph_result(_before_graph()), _graph_result(_after_graph())])
    calls = {"actuator": 0, "reconcile": 0}

    def graph_reader(*args, **kwargs):
        return next(graph_reads)

    def actuator(*args, **kwargs):
        calls["actuator"] += 1
        raise RuntimeError("connection dropped after possible dispatch")

    def reconciler(**kwargs):
        calls["reconcile"] += 1
        return {
            "state": "complete",
            "user_node_id": "user-new",
            "assistant_node_id": "assistant-new",
            "attachment_ids": ["file-xyz"],
        }

    receipt = capture_attachment_continue(
        "conversation-1",
        "ACCESSIBILITY-PROBE",
        [attachment],
        db_path=tmp_path / "capture.db",
        dependencies=CaptureDependencies(graph_reader=graph_reader, actuator=actuator, reconciler=reconciler),
    )

    assert calls["actuator"] == 1
    assert calls["reconcile"] == 1
    assert receipt["actuator_attempts"] == 1
    assert receipt["canonical_receipt"]["state"] == "complete"
    assert receipt["capture_error"]["stage"] == "actuator"
    assert receipt["capture_error"]["class"] == "RuntimeError"
    assert "connection dropped" not in str(receipt)


def test_after_graph_failure_returns_bounded_commit_unknown_without_resend(tmp_path: Path) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    calls = {"graph": 0, "actuator": 0, "reconcile": 0}

    def graph_reader(*args, **kwargs):
        calls["graph"] += 1
        if calls["graph"] == 1:
            return _graph_result(_before_graph())
        raise RuntimeError("graph unavailable TOKEN-SHOULD-NOT-LEAK")

    def actuator(*args, **kwargs):
        calls["actuator"] += 1
        return {"resolved_thread_id": "conversation-1", "protocol_observations": [], "stage_times": {}}

    def reconciler(**kwargs):
        calls["reconcile"] += 1
        raise RuntimeError("reconcile unavailable COOKIE-SHOULD-NOT-LEAK")

    receipt = capture_attachment_continue(
        "conversation-1",
        "ACCESSIBILITY-PROBE",
        [attachment],
        db_path=tmp_path / "capture.db",
        dependencies=CaptureDependencies(graph_reader=graph_reader, actuator=actuator, reconciler=reconciler),
    )

    assert calls["actuator"] == 1
    assert calls["reconcile"] == 1
    assert receipt["canonical_receipt"]["state"] == "commit_unknown"
    assert receipt["graph_after"] is None
    assert receipt["graph_delta"] is None
    assert receipt["capture_error"]["stage"] == "graph_after"
    rendered = str(receipt)
    assert "TOKEN-SHOULD-NOT-LEAK" not in rendered
    assert "COOKIE-SHOULD-NOT-LEAK" not in rendered
