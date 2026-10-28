from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from re_gpt.async_chatgpt import AsyncChatGPT
from re_gpt.errors import UnexpectedResponseError
from re_gpt.mcp_server import _normalized_timestamp, _unhydrated_targets_by_recency


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.json_called = False

    def json(self):
        self.json_called = True
        return self._payload


class _FakeAsyncSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = {}

    async def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_roster_and_thread_get_share_active_authenticated_async_session() -> None:
    roster = _FakeResponse(
        200,
        {"items": [{"id": "c1", "title": "One", "update_time": 1760000000.0}]},
    )
    thread = _FakeResponse(
        200,
        {"conversation_id": "c1", "current_node": "n1", "mapping": {"n1": {}}},
    )
    fake = _FakeAsyncSession([roster, thread])
    client = AsyncChatGPT(auth_token="owned-access-token", websocket_mode=False)
    client.session = fake

    async def run():
        page = await client.list_conversations_page(offset=0, limit=28)
        graph = await client.fetch_conversation("c1")
        return page, graph

    page, graph = asyncio.run(run())

    assert page["items"][0]["id"] == "c1"
    assert graph["current_node"] == "n1"
    assert len(fake.calls) == 2
    assert fake.calls[0]["url"].endswith("/backend-api/conversations")
    assert fake.calls[1]["url"].endswith("/backend-api/conversation/c1")
    for call in fake.calls:
        assert call["headers"]["Authorization"] == "Bearer owned-access-token"
        assert call["headers"]["Accept"] == "application/json"
        assert "Content-Type" not in call["headers"]
    assert roster.json_called is True
    assert thread.json_called is True


def test_roster_rejection_is_classified_before_json_parse() -> None:
    rejected = _FakeResponse(403, {"challenge": "must-not-be-parsed"})
    fake = _FakeAsyncSession([rejected])
    client = AsyncChatGPT(auth_token="owned-access-token", websocket_mode=False)
    client.session = fake

    async def run():
        await client.list_conversations_page(offset=0, limit=28)

    with pytest.raises(UnexpectedResponseError, match="HTTP 403"):
        asyncio.run(run())

    assert rejected.json_called is False


def _make_history_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                remote_update_time,
                last_seen_at
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                conversation_id TEXT,
                message_index INTEGER,
                author TEXT,
                content TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            [
                ("numeric-old", "Numeric old", 1750000000.0, 1750000000.0),
                ("iso-new", "ISO new", "2026-01-01T00:00:00Z", 0),
                ("iso-middle", "ISO middle", "2025-11-07T12:00:00+00:00", 0),
            ],
        )


def test_hydrate_recency_normalizes_iso_and_numeric_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    _make_history_db(db_path)

    targets = _unhydrated_targets_by_recency(db_path, limit=3)

    assert [conversation_id for conversation_id, _title in targets] == [
        "iso-new",
        "iso-middle",
        "numeric-old",
    ]
    assert _normalized_timestamp("2026-01-01T00:00:00Z") > _normalized_timestamp(1750000000.0)
    assert _normalized_timestamp("not-a-time") == float("-inf")
