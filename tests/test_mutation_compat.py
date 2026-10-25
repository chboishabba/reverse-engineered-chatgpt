from __future__ import annotations

from types import SimpleNamespace

import pytest

from re_gpt.mutation_compat import mutation_service_chat


class FakeService:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = []

    def continue_thread(self, conversation_id, prompt, **kwargs):
        self.calls.append(("continue", conversation_id, prompt, kwargs))
        return dict(self.result)

    def new_conversation(self, prompt, **kwargs):
        self.calls.append(("new", prompt, kwargs))
        return dict(self.result)


def _conversation(conversation_id="c1"):
    return SimpleNamespace(
        conversation_id=conversation_id,
        parent_id="a0" if conversation_id else None,
        model="gpt-6-thinking",
        chatgpt=SimpleNamespace(session_token="token", default_model="gpt-6-thinking"),
    )


def test_existing_sync_chat_delegates_to_api_first_mutation_service() -> None:
    service = FakeService({
        "state": "complete",
        "conversation_id": "c1",
        "user_node_id": "u1",
        "assistant_node_id": "a1",
        "assistant_text": "ACK",
    })
    conversation = _conversation("c1")
    chunks = list(mutation_service_chat(conversation, "hello", service=service))

    assert service.calls[0][0:3] == ("continue", "c1", "hello")
    assert service.calls[0][3]["write_transport"] == "async"
    assert service.calls[0][3]["dom_fallback"] is False
    assert chunks == [{"content": "ACK", "conversation_id": "c1", "message_id": "a1"}]
    assert conversation.parent_id == "a1"


def test_new_sync_chat_uses_service_new_and_updates_conversation_id() -> None:
    service = FakeService({
        "state": "complete",
        "conversation_id": "new-id",
        "user_node_id": "u1",
        "assistant_node_id": "a1",
        "assistant_text": "NEW",
    })
    conversation = _conversation(None)
    chunks = list(mutation_service_chat(conversation, "hello", service=service))

    assert service.calls[0][0:2] == ("new", "hello")
    assert conversation.conversation_id == "new-id"
    assert conversation.parent_id == "a1"
    assert chunks[0]["content"] == "NEW"


def test_uncertain_sync_chat_never_auto_retries() -> None:
    service = FakeService({"state": "commit_unknown", "operation_id": "op-x", "conversation_id": "c1"})
    with pytest.raises(RuntimeError, match="Do not resend"):
        list(mutation_service_chat(_conversation("c1"), "hello", service=service))
    assert len(service.calls) == 1
