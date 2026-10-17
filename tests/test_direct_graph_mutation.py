from __future__ import annotations

import json

from re_gpt.direct_graph_mutation import (
    build_graph_mutation_payload,
    extract_sse_identity,
)


def _base_payload(prompt: str = "hello") -> dict:
    return {
        "action": "next",
        "conversation_id": "old-conversation",
        "messages": [
            {
                "id": "user-client-id",
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]},
                "metadata": {},
            }
        ],
        "model": "gpt-6-thinking",
        "parent_message_id": "old-parent",
        "supported_encodings": ["v1"],
    }


def test_new_conversation_payload_omits_conversation_id_and_uses_client_root() -> None:
    payload = build_graph_mutation_payload(
        _base_payload("new prompt"),
        operation="new_conversation",
        conversation_id=None,
        parent_node_id=None,
        prompt="new prompt",
    )
    assert payload["action"] == "next"
    assert "conversation_id" not in payload
    assert payload["parent_message_id"] == "client-created-root"
    assert payload["messages"][0]["content"]["parts"] == ["new prompt"]


def test_branch_payload_targets_exact_parent_node() -> None:
    payload = build_graph_mutation_payload(
        _base_payload("branch prompt"),
        operation="branch",
        conversation_id="conversation-1",
        parent_node_id="assistant-target",
        prompt="branch prompt",
    )
    assert payload["action"] == "next"
    assert payload["conversation_id"] == "conversation-1"
    assert payload["parent_message_id"] == "assistant-target"
    assert payload["messages"][0]["id"] == "user-client-id"


def test_regenerate_payload_is_variant_without_new_user_message() -> None:
    payload = build_graph_mutation_payload(
        _base_payload("ignored"),
        operation="regenerate",
        conversation_id="conversation-1",
        parent_node_id="user-target",
        prompt=None,
    )
    assert payload["action"] == "variant"
    assert payload["conversation_id"] == "conversation-1"
    assert payload["parent_message_id"] == "user-target"
    assert payload["variant_purpose"] == "comparison_implicit"
    assert "messages" not in payload


def test_edit_branch_uses_next_from_original_users_parent() -> None:
    payload = build_graph_mutation_payload(
        _base_payload("replacement"),
        operation="edit_branch",
        conversation_id="conversation-1",
        parent_node_id="before-original-user",
        prompt="replacement",
    )
    assert payload["action"] == "next"
    assert payload["parent_message_id"] == "before-original-user"
    assert payload["messages"][0]["content"]["parts"] == ["replacement"]


def test_extract_sse_identity_finds_new_conversation_and_assistant() -> None:
    chunks = [
        'data: {"conversation_id":"new-conversation","message":{"id":"user-id","author":{"role":"user"}}}\n',
        'data: {"conversation_id":"new-conversation","message":{"id":"assistant-id","author":{"role":"assistant"},"content":{"content_type":"text","parts":["ok"]}}}\n',
        "data: [DONE]\n",
    ]
    identity = extract_sse_identity(chunks)
    assert identity["conversation_id"] == "new-conversation"
    assert identity["assistant_message_id"] == "assistant-id"
    assert identity["user_message_id"] == "user-id"


def test_extract_sse_identity_handles_events_fragmented_across_chunks() -> None:
    chunks = [
        b'data: {"conversation_id":"new-con',
        b'versation","message":{"id":"assistant-id",',
        b'"author":{"role":"assistant"}}}\n\ndata: [DONE]\n',
    ]
    identity = extract_sse_identity(chunks)
    assert identity["conversation_id"] == "new-conversation"
    assert identity["assistant_message_id"] == "assistant-id"


def test_sse_identity_does_not_copy_message_text() -> None:
    chunks = [
        'data: {"conversation_id":"c1","message":{"id":"a1","author":{"role":"assistant"},"content":{"parts":["SECRET-TEXT"]}}}\n'
    ]
    rendered = json.dumps(extract_sse_identity(chunks), sort_keys=True)
    assert "SECRET-TEXT" not in rendered