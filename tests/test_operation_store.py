from __future__ import annotations

from re_gpt.operation_store import MutationOperationStore


def test_operation_store_persists_state_and_prompt_digest(tmp_path) -> None:
    db = tmp_path / "history.sqlite3"
    with MutationOperationStore(db) as store:
        created = store.create(operation="continue", conversation_id="abc", prompt="hello")
        operation_id = created["operation_id"]
        assert created["state"] == "created"
        assert created["prompt_digest"] != "hello"
        updated = store.update(
            operation_id,
            state="committed_pending",
            user_node_id="user-1",
            committed_at=123.0,
        )
        assert updated["user_node_id"] == "user-1"

    with MutationOperationStore(db) as reopened:
        persisted = reopened.get(operation_id)
        assert persisted is not None
        assert persisted["state"] == "committed_pending"
        assert persisted["user_node_id"] == "user-1"


def test_operation_store_does_not_store_prompt_body(tmp_path) -> None:
    db = tmp_path / "history.sqlite3"
    secret_prompt = "this body belongs only in the conversation graph"
    with MutationOperationStore(db) as store:
        created = store.create(operation="continue", conversation_id="abc", prompt=secret_prompt)
        row = store.get(created["operation_id"])
        assert row is not None
        assert secret_prompt not in repr(row)
