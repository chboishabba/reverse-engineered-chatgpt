from __future__ import annotations

import json
from pathlib import Path

from re_gpt.attachments import LocalAttachment
from re_gpt.experiment_receipt import (
    build_experiment_receipt,
    diff_graphs,
    graph_snapshot,
    project_graph,
    serialize_experiment_receipt,
    summarize_protocol_stages,
)


def _graph(*, with_attachment: bool = False, assistant: bool = False) -> dict:
    user_metadata = {
        "safe_flag": True,
        "secret_marker": "SENTINEL-SECRET-SHOULD-NOT-LEAK",
    }
    if with_attachment:
        user_metadata["attachments"] = [
            {
                "id": "file-123",
                "name": "probe.txt",
                "mime_type": "text/plain",
                "size": 12,
                "asset_pointer": "sediment://file-123",
                "private": "ATTACHMENT-SECRET-SHOULD-NOT-LEAK",
            }
        ]
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
        "user": {
            "id": "user",
            "parent": "root",
            "children": ["assistant"] if assistant else [],
            "message": {
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["TRANSCRIPT-SECRET-SHOULD-NOT-LEAK"]},
                "status": "finished_successfully",
                "end_turn": True,
                "create_time": 10.0,
                "update_time": 11.0,
                "metadata": user_metadata,
            },
        },
    }
    current = "user"
    if assistant:
        mapping["assistant"] = {
            "id": "assistant",
            "parent": "user",
            "children": [],
            "message": {
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": ["ASSISTANT-SECRET-SHOULD-NOT-LEAK"]},
                "status": "finished_successfully",
                "end_turn": True,
                "create_time": 12.0,
                "metadata": {"model_slug": "model-x"},
            },
        }
        current = "assistant"
    return {"id": "conversation-1", "current_node": current, "mapping": mapping}


def test_project_graph_is_structural_and_redacted() -> None:
    projected = project_graph(_graph(with_attachment=True))
    rendered = json.dumps(projected, sort_keys=True)

    assert projected["current_node"] == "user"
    assert projected["nodes"]["root"]["role"] is None
    assert projected["nodes"]["user"]["role"] == "user"
    assert projected["nodes"]["user"]["content_type"] == "text"
    assert projected["nodes"]["user"]["metadata_keys"] == ["attachments", "safe_flag", "secret_marker"]
    assert projected["nodes"]["user"]["attachments"] == [
        {
            "file_id": "file-123",
            "filename": "probe.txt",
            "mime_type": "text/plain",
            "size": 12,
            "pointer_present": True,
            "pointer_type": "sediment",
        }
    ]
    assert "TRANSCRIPT-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "SENTINEL-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "ATTACHMENT-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "sediment://file-123" not in rendered


def test_graph_snapshot_hashes_only_projection() -> None:
    first = graph_snapshot(_graph())
    mutated = _graph()
    mutated["mapping"]["user"]["message"]["content"]["parts"] = ["DIFFERENT SECRET TEXT"]
    second = graph_snapshot(mutated)
    assert first["digest"] == second["digest"]
    assert first["node_count"] == 2


def test_diff_graphs_reports_structural_transition_and_attachment() -> None:
    before = _graph()
    after = _graph(with_attachment=True, assistant=True)
    delta = diff_graphs(before, after)

    assert delta["added_nodes"] == ["assistant"]
    assert delta["removed_nodes"] == []
    assert delta["current_node_before"] == "user"
    assert delta["current_node_after"] == "assistant"
    assert ["user", "assistant"] in delta["added_edges"]
    assert any(item["node_id"] == "user" and "attachments" in item["fields"] for item in delta["changed_nodes"])
    assert delta["added_attachments"] == [
        {
            "node_id": "user",
            "file_id": "file-123",
            "filename": "probe.txt",
            "mime_type": "text/plain",
            "size": 12,
            "pointer_present": True,
            "pointer_type": "sediment",
        }
    ]


def test_summarize_protocol_stages_preserves_order_without_values() -> None:
    observations = [
        {
            "sequence_index": 2,
            "bootstrap_stage": "conversation_post",
            "method": "POST",
            "path": "/backend-api/conversation",
            "status": 200,
            "response_class": "sse_stream",
            "header_names": ["authorization", "content-type"],
            "cookie_names": ["session-token"],
            "body_keys": ["messages", "parent_message_id"],
        },
        {
            "sequence_index": 1,
            "bootstrap_stage": None,
            "method": "POST",
            "path": "/backend-api/files",
            "status": 201,
            "response_class": "json_ok",
            "body_keys": ["file_name"],
        },
    ]
    summary = summarize_protocol_stages(observations)
    assert [item["sequence_index"] for item in summary] == [1, 2]
    assert summary[0]["stage"] == "unclassified"
    assert summary[1]["stage"] == "conversation_post"
    rendered = json.dumps(summary)
    assert "session-token" not in rendered
    assert "authorization" not in rendered


def test_experiment_receipt_serialization_drops_paths_and_secret_values(tmp_path: Path) -> None:
    path = tmp_path / "probe.txt"
    path.write_text("hello world\n", encoding="utf-8")
    local = LocalAttachment.from_path(path)

    receipt = build_experiment_receipt(
        operation="attachment_continue",
        actuator="browser_dom",
        conversation_id="conversation-1",
        started_at=1.0,
        completed_at=2.0,
        local_attachments=[local],
        protocol_observations=[
            {
                "sequence_index": 1,
                "bootstrap_stage": "conversation_post",
                "method": "POST",
                "path": "/backend-api/conversation",
                "status": 200,
                "response_class": "sse_stream",
                "raw_secret": "BEARER-SECRET-SHOULD-NOT-LEAK",
            }
        ],
        graph_before=_graph(),
        graph_after=_graph(with_attachment=True, assistant=True),
        canonical_receipt={
            "state": "complete",
            "user_node_id": "user",
            "assistant_node_id": "assistant",
            "attachment_ids": ["file-123"],
            "error_detail": "TOKEN-SECRET-SHOULD-NOT-LEAK",
        },
        read_receipts={
            "before": {"adapter": "async", "diagnostic": {"secret": "COOKIE-SECRET-SHOULD-NOT-LEAK"}},
            "after": {"adapter": "async"},
        },
        actuator_attempts=1,
    )
    rendered = serialize_experiment_receipt(receipt)
    parsed = json.loads(rendered)

    assert parsed["schema_version"] == 1
    assert parsed["actuator_attempts"] == 1
    assert parsed["local_inputs"]["attachments"][0]["filename"] == "probe.txt"
    assert str(path) not in rendered
    assert "BEARER-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "TOKEN-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "COOKIE-SECRET-SHOULD-NOT-LEAK" not in rendered
    assert "TRANSCRIPT-SECRET-SHOULD-NOT-LEAK" not in rendered
