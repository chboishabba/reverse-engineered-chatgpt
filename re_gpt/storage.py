"""Persistence helpers for ChatGPT conversations."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_DB_PATH = Path.home() / ".chatgpt_history.sqlite3"
DEFAULT_EXPORT_DIR = Path("chat_exports")


def extract_ordered_messages(chat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract and order messages from a conversation mapping."""

    messages: list[dict[str, Any]] = []

    mapping = chat.get("mapping") if isinstance(chat, Mapping) else None
    if not isinstance(mapping, Mapping):
        return messages

    for node in mapping.values():
        if not isinstance(node, Mapping):
            continue

        message = node.get("message")
        if not isinstance(message, Mapping):
            continue

        author_info = message.get("author")
        author = ""
        if isinstance(author_info, Mapping):
            author = str(author_info.get("role") or "")

        content_info = message.get("content")
        parts: list[str] = []
        if isinstance(content_info, Mapping):
            raw_parts = content_info.get("parts") or []
            if isinstance(raw_parts, Iterable) and not isinstance(raw_parts, (str, bytes)):
                for part in raw_parts:
                    text = ""
                    if isinstance(part, str):
                        text = part.strip()
                    elif isinstance(part, Mapping):
                        for key in ("text", "content", "title"):
                            value = part.get(key)
                            if value:
                                text = str(value).strip()
                                break
                    if text:
                        parts.append(text)

        if not parts:
            continue

        create_time = message.get("create_time")
        if not isinstance(create_time, (int, float)):
            create_time = 0

        messages.append(
            {
                "author": author,
                "content": "\n".join(parts),
                "create_time": create_time,
            }
        )

    messages.sort(key=lambda item: item.get("create_time", 0))
    for index, message in enumerate(messages):
        message["message_index"] = index
    return messages


class ConversationStorage:
    """Persist conversations to JSON files and a SQLite database."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        export_dir: Path | str = DEFAULT_EXPORT_DIR,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.export_dir = Path(export_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.execute("PRAGMA journal_mode=WAL;")
        self._connection.execute("PRAGMA foreign_keys=ON;")
        self._initialise_schema()

    def __enter__(self) -> "ConversationStorage":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._connection.close()

    def _initialise_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    conversation_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    create_time REAL,
                    PRIMARY KEY (conversation_id, message_index, author)
                )
                """
            )

    def persist_chat(
        self,
        conversation_id: str,
        chat: Mapping[str, Any],
        messages: Iterable[Mapping[str, Any]] | None = None,
    ) -> Path:
        """Persist a conversation to JSON and the database."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        if messages is None:
            messages = extract_ordered_messages(chat)
        self.upsert_messages(conversation_id, messages)
        return self.export_conversation(conversation_id, chat)

    def export_conversation(self, conversation_id: str, chat: Mapping[str, Any]) -> Path:
        """Write ``chat`` to ``conversation_<id>.json`` in ``export_dir``."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        self.export_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.export_dir / f"conversation_{conversation_id}.json"
        with json_path.open("w", encoding="utf-8") as handle:
            json.dump(chat, handle, indent=2, ensure_ascii=False)
        return json_path

    def upsert_messages(
        self, conversation_id: str, messages: Iterable[Mapping[str, Any]]
    ) -> None:
        """Insert or update messages for ``conversation_id``."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        with self._connection:
            for message in messages:
                index = int(message.get("message_index", 0))
                author = str(message.get("author") or "")
                content = str(message.get("content") or "")
                create_time = message.get("create_time")
                self._connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id,
                        message_index,
                        author,
                        content,
                        create_time
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, message_index, author)
                    DO UPDATE SET
                        content = excluded.content,
                        create_time = excluded.create_time
                    """,
                    (conversation_id, index, author, content, create_time),
                )

    def append_message(
        self,
        conversation_id: str,
        author: str,
        content: str,
        create_time: float | None = None,
    ) -> int:
        """Append a message and return its message index."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        if create_time is None:
            create_time = time.time()

        with self._connection:
            cursor = self._connection.execute(
                "SELECT COALESCE(MAX(message_index), -1) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            next_index = int(cursor.fetchone()[0]) + 1
            self._connection.execute(
                """
                INSERT INTO messages (
                    conversation_id,
                    message_index,
                    author,
                    content,
                    create_time
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, message_index, author)
                DO UPDATE SET
                    content = excluded.content,
                    create_time = excluded.create_time
                """,
                (conversation_id, next_index, author, content, create_time),
            )
        return next_index

