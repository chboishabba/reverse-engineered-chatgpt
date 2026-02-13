import os
import sys
import asyncio
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.async_chatgpt import AsyncChatGPT


def test_sync_list_all_conversations_pagination(monkeypatch):
    client = SyncChatGPT()

    pages = [
        {
            "items": [
                {"id": "1", "title": "a", "update_time": 1},
                {"id": "2", "title": "b", "update_time": 2},
            ]
        },
        {"items": [{"id": "3", "title": "c", "update_time": 3}]},
    ]
    calls = []

    def fake_page(offset=0, limit=28):
        calls.append((offset, limit))
        return pages.pop(0)

    monkeypatch.setattr(client, "list_conversations_page", fake_page)

    result = client.list_all_conversations(limit=2)

    assert result == [
        {"id": "1", "title": "a", "last_updated": 1},
        {"id": "2", "title": "b", "last_updated": 2},
        {"id": "3", "title": "c", "last_updated": 3},
    ]
    assert calls == [(0, 2), (2, 2)]


def test_async_list_all_conversations_pagination(monkeypatch):
    client = AsyncChatGPT()

    pages = [
        {
            "items": [
                {"id": "1", "title": "a", "update_time": 1},
                {"id": "2", "title": "b", "update_time": 2},
            ]
        },
        {"items": [{"id": "3", "title": "c", "update_time": 3}]},
    ]
    calls = []

    async def fake_page(offset=0, limit=28):
        calls.append((offset, limit))
        return pages.pop(0)

    monkeypatch.setattr(client, "list_conversations_page", fake_page)

    result = asyncio.run(client.list_all_conversations(limit=2))

    assert result == [
        {"id": "1", "title": "a", "last_updated": 1},
        {"id": "2", "title": "b", "last_updated": 2},
        {"id": "3", "title": "c", "last_updated": 3},
    ]
    assert calls == [(0, 2), (2, 2)]


def test_async_fetch_conversation_removes_stream_headers(monkeypatch):
    client = AsyncChatGPT()
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    class FakeSession:
        async def get(self, url=None, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["params"] = dict(params or {})
            return FakeResponse()

    monkeypatch.setattr(client, "build_request_headers", lambda: {"Content-Type": "application/json"})
    client.session = FakeSession()

    result = asyncio.run(client.fetch_conversation("conv-1", since_time=12.5))

    assert result == {"ok": True}
    assert captured["url"].endswith("/conversation/conv-1")
    assert "Content-Type" not in captured["headers"]
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["params"]["since_time"] == "12.5"
