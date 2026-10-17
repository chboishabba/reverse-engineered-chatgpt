from __future__ import annotations

import scripts.accept_graph_mutations_v1 as acceptance


class FakeService:
    def __init__(self) -> None:
        self.calls = []

    def new_conversation(self, prompt, **kwargs):
        self.calls.append(("new", prompt))
        return {
            "state": "complete",
            "operation_id": "op-new",
            "conversation_id": "c-new",
            "user_node_id": "u-new",
            "assistant_node_id": "a-new",
            "assistant_text": "NEW-ACK",
        }

    def branch_from_node(self, conversation_id, parent_node_id, prompt, **kwargs):
        self.calls.append(("branch", conversation_id, parent_node_id, prompt))
        return {
            "state": "complete",
            "operation_id": "op-branch",
            "conversation_id": conversation_id,
            "user_node_id": "u-branch",
            "assistant_node_id": "a-branch",
            "assistant_text": "BRANCH-ACK",
        }

    def regenerate(self, conversation_id, user_node_id, **kwargs):
        self.calls.append(("regenerate", conversation_id, user_node_id))
        return {
            "state": "complete",
            "operation_id": "op-regen",
            "conversation_id": conversation_id,
            "user_node_id": user_node_id,
            "assistant_node_id": "a-regen",
            "assistant_text": "REGEN-ACK",
        }

    def edit_and_branch(self, conversation_id, user_node_id, prompt, **kwargs):
        self.calls.append(("edit", conversation_id, user_node_id, prompt))
        return {
            "state": "complete",
            "operation_id": "op-edit",
            "conversation_id": conversation_id,
            "user_node_id": "u-edit",
            "assistant_node_id": "a-edit",
            "assistant_text": "EDIT-ACK",
        }


def test_acceptance_runs_graph_mutations_in_dependency_order() -> None:
    service = FakeService()
    result = acceptance.run_acceptance(service, nonce="N1")

    assert [item["name"] for item in result["operations"]] == [
        "new_conversation",
        "branch",
        "regenerate",
        "edit_and_branch",
    ]
    assert service.calls == [
        ("new", "Reply with only NEW-N1"),
        ("branch", "c-new", "a-new", "Reply with only BRANCH-N1"),
        ("regenerate", "c-new", "u-new"),
        ("edit", "c-new", "u-new", "Reply with only EDIT-N1"),
    ]
    assert result["conversation_id"] == "c-new"
    assert result["all_complete"] is True


def test_acceptance_stops_before_dependent_mutation_when_new_is_not_canonical() -> None:
    class UnknownService(FakeService):
        def new_conversation(self, prompt, **kwargs):
            self.calls.append(("new", prompt))
            return {"state": "commit_unknown", "operation_id": "op-new"}

    service = UnknownService()
    result = acceptance.run_acceptance(service, nonce="N2")
    assert len(result["operations"]) == 1
    assert result["all_complete"] is False
    assert service.calls == [("new", "Reply with only NEW-N2")]
