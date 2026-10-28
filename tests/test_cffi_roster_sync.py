from __future__ import annotations

import sqlite3
from pathlib import Path

from re_gpt import mcp_server
from re_gpt.sync_chatgpt import SyncChatGPT
import re_gpt.sync_chatgpt as sync_module


def _seed_history(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE conversations (
                internal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL UNIQUE,
                conversation_key TEXT NOT NULL,
                title TEXT,
                discovered_at REAL NOT NULL DEFAULT 0,
                last_seen_at REAL NOT NULL DEFAULT 0,
                remote_update_time,
                cached_message_count INTEGER DEFAULT 0
            )
            """
        )
        db.execute(
            """
            CREATE TABLE messages (
                conversation_id TEXT NOT NULL,
                message_index INTEGER NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                create_time REAL,
                message_key TEXT
            )
            """
        )
        rows = [
            ("iso-new", "k1", "ISO New", 0.0, "2026-10-28T23:30:00+10:00"),
            ("epoch-old", "k2", "Epoch Old", 0.0, 1760000000.0),
            ("iso-old", "k3", "ISO Old", 0.0, "2025-01-01T00:00:00Z"),
        ]
        db.executemany(
            """
            INSERT INTO conversations
            (conversation_id, conversation_key, title, last_seen_at, remote_update_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def test_hydrate_recency_normalizes_iso_and_epoch_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    _seed_history(db_path)

    targets = mcp_server._unhydrated_targets_by_recency(db_path, limit=3)

    assert [conversation_id for conversation_id, _title in targets] == [
        "iso-new",
        "epoch-old",
        "iso-old",
    ]


def test_roster_sync_uses_authenticated_async_client_and_persists(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    calls: list[tuple[int, int]] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_conversations_page(self, offset=0, limit=28):
            calls.append((offset, limit))
            if offset == 0:
                return {
                    "items": [
                        {"id": "c1", "title": "One", "update_time": "2026-10-28T20:00:00Z"},
                        {"id": "c2", "title": "Two", "update_time": 1760000000.0},
                    ]
                }
            return {"items": []}

    monkeypatch.setattr(mcp_server, "AsyncChatGPT", FakeAsyncClient)
    monkeypatch.setattr(mcp_server, "get_session_token", lambda: "session")
    monkeypatch.setattr(mcp_server, "get_default_model", lambda: "model")
    monkeypatch.setattr(mcp_server, "get_default_user_agent", lambda: "ua")
    monkeypatch.setattr(mcp_server, "load_browser_session_snapshot", lambda **_kwargs: object())

    result = mcp_server._sync_roster_via_async_cffi(
        db_path=db_path,
        page_limit=2,
        max_conversations=10,
    )

    assert result["transport"] == "async_curl_cffi"
    assert result["browser_fallback"] is False
    assert result["sync_fallback"] is False
    assert result["conversation_count"] == 2
    assert calls == [(0, 2), (2, 2)]

    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT conversation_id, remote_update_time FROM conversations ORDER BY conversation_id"
        ).fetchall()
    assert [row[0] for row in rows] == ["c1", "c2"]
    assert all(isinstance(row[1], float) for row in rows)


def test_sync_roster_page_delegates_to_async_client(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_conversations_page(self, offset=0, limit=28):
            captured["page"] = (offset, limit)
            return {"items": [{"id": "c1"}]}

    monkeypatch.setattr(sync_module, "AsyncChatGPT", FakeAsyncClient)

    client = SyncChatGPT(
        session_token="session",
        auth_token="auth",
        user_agent="ua",
        websocket_mode=False,
    )
    client.session = object()

    result = client.list_conversations_page(offset=4, limit=7)

    assert result == {"items": [{"id": "c1"}]}
    assert captured["page"] == (4, 7)
    kwargs = captured["kwargs"]
    assert kwargs["session_token"] == "session"
    assert kwargs["auth_token"] == "auth"
    assert kwargs["user_agent"] == "ua"
