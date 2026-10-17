from __future__ import annotations

from pathlib import Path

import re_gpt.mutation_service as mutation_service_module
from re_gpt.mutation_service import MutationService


def _message(role: str, text: str, created: float) -> dict:
    return {
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [text]},
        "status": "finished_successfully",
        "end_turn": True,
        "create_time": created,
        "metadata": {},
    }


def _graph_for_new() -> dict:
    return {
        "id": "new-conversation",
        "current_node": "a1",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
            "u1": {"id": "u1", "parent": "root", "children": ["a1"], "message": _message("user", "hello", 100.0)},
            "a1": {"id": "a1", "parent": "u1", "children": [], "message": _message("assistant", "world", 101.0)},
        },
    }


def _existing_graph(*, replacement: bool = False, regenerated: bool = False) -> dict:
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["u0"], "message": None},
        "u0": {"id": "u0", "parent": "root", "children": ["a0"], "message": _message("user", "original", 10.0)},
        "a0": {"id": "a0", "parent": "u0", "children": [], "message": _message("assistant", "old", 11.0)},
    }
    current = "a0"
    if replacement:
        mapping["root"]["children"].append("u1")
        mapping["u1"] = {"id": "u1", "parent": "root", "children": ["a1"], "message": _message("user", "replacement", 100.0)}
        mapping["a1"] = {"id": "a1", "parent": "u1", "children": [], "message": _message("assistant", "new", 101.0)}
        current = "a1"
    if regenerated:
        mapping["u0"]["children"].append("a1")
        mapping["a1"] = {"id": "a1", "parent": "u0", "children": [], "message": _message("assistant", "regenerated", 101.0)}
        current = "a1"
    return {"id": "conversation-1", "current_node": current, "mapping": mapping}


def _pulled(graph: dict) -> dict:
    return {
        "chat": graph,
        "new_messages": 2,
        "total_messages": len(graph["mapping"]),
        "read_adapter": "async",
        "read_diagnostic": {},
        "attempted_read_adapters": ["async"],
        "rejected_read_adapters": [],
        "read_observations": [],
    }


def test_new_conversation_captures_server_id_and_reconciles(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    def fake_submit(operation, **kwargs):
        seen.update({"operation": operation, **kwargs})
        return {
            "resolved_thread_id": "new-conversation",
            "conversation_id": None,
            "user_message_id": "u1",
            "assistant_message_id": "a1",
            "stage_times": {"submission_observed": 10.0},
            "protocol_observations": [],
        }

    monkeypatch.setattr(mutation_service_module, "submit_direct_graph_mutation_once", fake_submit)
    service = MutationService(db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(service, "_pull_and_persist", lambda conversation_id, **kwargs: _pulled(_graph_for_new()))

    result = service.new_conversation("hello", follow=False)
    assert seen["operation"] == "new_conversation"
    assert seen["conversation_id"] is None
    assert result["state"] == "complete"
    assert result["conversation_id"] == "new-conversation"
    assert result["user_node_id"] == "u1"
    assert result["assistant_node_id"] == "a1"


def test_branch_targets_exact_parent_node(tmp_path: Path, monkeypatch) -> None:
    seen = {}
    before = _existing_graph()
    after = _existing_graph(replacement=True)

    def fake_submit(operation, **kwargs):
        seen.update({"operation": operation, **kwargs})
        return {"resolved_thread_id": "conversation-1", "user_message_id": "u1", "stage_times": {}, "protocol_observations": []}

    monkeypatch.setattr(mutation_service_module, "submit_direct_graph_mutation_once", fake_submit)
    service = MutationService(db_path=tmp_path / "db.sqlite")
    reads = iter([_pulled(before), _pulled(after)])
    monkeypatch.setattr(service, "_pull_and_persist", lambda *args, **kwargs: next(reads))

    result = service.branch_from_node("conversation-1", "root", "replacement")
    assert seen["operation"] == "branch"
    assert seen["parent_node_id"] == "root"
    assert result["state"] == "complete"
    assert result["user_node_id"] == "u1"


def test_regenerate_creates_new_assistant_variant_under_exact_user(tmp_path: Path, monkeypatch) -> None:
    seen = {}
    reads = iter([_pulled(_existing_graph()), _pulled(_existing_graph(regenerated=True))])

    def fake_submit(operation, **kwargs):
        seen.update({"operation": operation, **kwargs})
        return {"resolved_thread_id": "conversation-1", "assistant_message_id": "a1", "stage_times": {}, "protocol_observations": []}

    monkeypatch.setattr(mutation_service_module, "submit_direct_graph_mutation_once", fake_submit)
    service = MutationService(db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(service, "_pull_and_persist", lambda *args, **kwargs: next(reads))

    result = service.regenerate("conversation-1", "u0")
    assert seen["operation"] == "regenerate"
    assert seen["parent_node_id"] == "u0"
    assert seen["prompt"] is None
    assert result["state"] == "complete"
    assert result["user_node_id"] == "u0"
    assert result["assistant_node_id"] == "a1"


def test_edit_and_branch_uses_original_users_parent(tmp_path: Path, monkeypatch) -> None:
    seen = {}
    reads = iter([_pulled(_existing_graph()), _pulled(_existing_graph(replacement=True))])

    def fake_submit(operation, **kwargs):
        seen.update({"operation": operation, **kwargs})
        return {"resolved_thread_id": "conversation-1", "user_message_id": "u1", "stage_times": {}, "protocol_observations": []}

    monkeypatch.setattr(mutation_service_module, "submit_direct_graph_mutation_once", fake_submit)
    service = MutationService(db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(service, "_pull_and_persist", lambda *args, **kwargs: next(reads))

    result = service.edit_and_branch("conversation-1", "u0", "replacement")
    assert seen["operation"] == "edit_branch"
    assert seen["parent_node_id"] == "root"
    assert result["state"] == "complete"
    assert result["user_node_id"] == "u1"
