from __future__ import annotations

from types import SimpleNamespace

import pytest

from re_gpt import cli, entrypoint
from re_gpt.sync_chatgpt import SyncConversation


CID = "6acc564d-0314-83ec-8036-1f4d9868c5f3"


class DummyStorage:
    def record_conversations(self, items):
        raise AssertionError("UUID reads must not fetch or persist a remote catalog")

    def count_messages(self, conversation_id):
        return 0


class DummySyncClient:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload or {
            "title": "Demo",
            "current_node": "assistant-node",
            "mapping": {
                "user-node": {"message": {"author": {"role": "user"}, "content": {"parts": ["hello"]}}},
                "assistant-node": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["world"]}}},
            },
        }
        self.error = error
        self.fetch_calls = []
        self.catalog_calls = 0

    def fetch_conversation(self, conversation_id, **kwargs):
        self.fetch_calls.append((conversation_id, kwargs))
        if self.error:
            raise self.error
        return self.payload

    def list_all_conversations(self):
        self.catalog_calls += 1
        raise AssertionError("UUID reads must not fall back to list_all_conversations")

    def list_conversations_page(self, *args, **kwargs):
        self.catalog_calls += 1
        raise AssertionError("UUID reads must not fall back to catalog pagination")

    def get_conversation(self, conversation_id, title=None):
        raise AssertionError("validated single-ID reads must not use legacy get_conversation().fetch_chat()")


def test_syncconversation_fetch_chat_delegates_to_validated_parent_fetcher() -> None:
    client = DummySyncClient()
    conversation = SyncConversation(client, CID)

    chat = conversation.fetch_chat()

    assert chat is client.payload
    assert client.fetch_calls == [(CID, {})]
    assert conversation.parent_id == "assistant-node"
    assert conversation.title == "Demo"


def test_uuid_view_uses_one_validated_fetch_and_never_catalog_fallback(capsys) -> None:
    client = DummySyncClient(error=RuntimeError("HTTP 429: Too many requests"))

    cli.run_noninteractive_view(CID, client, DummyStorage())

    assert client.fetch_calls == [(CID, {})]
    assert client.catalog_calls == 0
    output = capsys.readouterr().out
    assert "HTTP 429: Too many requests" in output
    assert "trying to match by title" not in output


def test_uuid_download_uses_validated_fetch_without_catalog(monkeypatch, capsys) -> None:
    client = DummySyncClient()
    persisted = []

    class Storage(DummyStorage):
        def persist_chat(self, conversation_id, chat, messages, asset_fetcher=None):
            persisted.append((conversation_id, chat))
            return SimpleNamespace(
                new_messages=len(messages), total_messages=len(messages),
                asset_paths=[], asset_errors=[], json_path=None,
            )

    monkeypatch.setattr(cli, "extract_ordered_messages", lambda chat: [{"message_index": 0}])
    cli.handle_download_command(f"download {CID}", client, Storage())

    assert client.fetch_calls == [(CID, {})]
    assert client.catalog_calls == 0
    assert persisted and persisted[0][0] == CID
    assert "Saved conversation" in capsys.readouterr().out


def test_reconciliation_uses_validated_sync_fetch(monkeypatch) -> None:
    client = DummySyncClient()

    class ChatContext:
        def __enter__(self):
            return client
        def __exit__(self, *exc):
            return False

    class StorageContext:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def persist_chat(self, conversation_id, chat, messages, asset_fetcher=None):
            return SimpleNamespace(
                new_messages=1, total_messages=1, asset_paths=[], asset_errors=[], json_path=None
            )

    monkeypatch.setattr(entrypoint, "get_session_token", lambda: "token")
    monkeypatch.setattr(entrypoint, "get_default_model", lambda: "auto")
    monkeypatch.setattr(entrypoint, "get_default_user_agent", lambda: "ua")
    monkeypatch.setattr(entrypoint, "SyncChatGPT", lambda **kwargs: ChatContext())
    monkeypatch.setattr(entrypoint, "ConversationStorage", lambda **kwargs: StorageContext())
    monkeypatch.setattr(entrypoint, "extract_ordered_messages", lambda chat: [{"message_index": 0}])

    result = entrypoint.reconcile_conversation(CID)

    assert client.fetch_calls == [(CID, {})]
    assert result["conversation_id"] == CID
    assert result["new_messages"] == 1
