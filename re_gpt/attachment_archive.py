"""Canonical attachment metadata persistence in the existing re-gpt SQLite DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Mapping, Any

from .attachments import iter_canonical_attachments
from .storage import DEFAULT_DB_PATH


class AttachmentArchive:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()
    def close(self) -> None: self.connection.close()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS conversation_attachments (
                    conversation_id TEXT NOT NULL,
                    message_node_id TEXT NOT NULL,
                    file_id TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT,
                    size_bytes INTEGER,
                    asset_pointer TEXT,
                    local_path TEXT,
                    local_sha256 TEXT,
                    PRIMARY KEY (conversation_id, message_node_id, file_id, filename)
                )
            """)
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_conversation_attachments_file ON conversation_attachments(file_id)")

    def persist_graph(self, conversation_id: str, chat: Mapping[str, Any]) -> int:
        rows = iter_canonical_attachments(chat)
        with self.connection:
            for item in rows:
                self.connection.execute("""
                    INSERT INTO conversation_attachments (
                        conversation_id, message_node_id, file_id, filename,
                        mime_type, size_bytes, asset_pointer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, message_node_id, file_id, filename)
                    DO UPDATE SET
                        mime_type = COALESCE(excluded.mime_type, conversation_attachments.mime_type),
                        size_bytes = COALESCE(excluded.size_bytes, conversation_attachments.size_bytes),
                        asset_pointer = COALESCE(excluded.asset_pointer, conversation_attachments.asset_pointer)
                """, (
                    conversation_id, item.node_id, item.file_id, item.filename,
                    item.mime_type, item.size, item.pointer,
                ))
        return len(rows)

    def bind_local_copy(
        self,
        conversation_id: str,
        message_node_id: str,
        file_id: str,
        filename: str,
        local_path: str,
        sha256: str,
    ) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE conversation_attachments
                SET local_path = ?, local_sha256 = ?
                WHERE conversation_id = ? AND message_node_id = ?
                  AND file_id = ? AND filename = ?
            """, (local_path, sha256, conversation_id, message_node_id, file_id, filename))

    def rows_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT * FROM conversation_attachments
            WHERE conversation_id = ?
            ORDER BY message_node_id, filename, file_id
        """, (conversation_id,)).fetchall()
        return [dict(row) for row in rows]
