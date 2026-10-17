"""Durable mutation-attempt state in the canonical re-gpt SQLite database."""

from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .storage import DEFAULT_DB_PATH


TERMINAL_STATES = {"complete", "failed_precommit", "commit_unknown", "committed_pending"}


class MutationOperationStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def __enter__(self) -> "MutationOperationStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mutation_attempts (
                    operation_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    conversation_id TEXT,
                    requested_parent_node_id TEXT,
                    prompt_digest TEXT NOT NULL,
                    transport TEXT NOT NULL DEFAULT 'dom',
                    state TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    authenticated_at REAL,
                    hydrated_at REAL,
                    submitted_at REAL,
                    committed_at REAL,
                    completed_at REAL,
                    user_node_id TEXT,
                    assistant_node_id TEXT,
                    error_class TEXT,
                    error_detail TEXT
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mutation_attempts_conversation ON mutation_attempts(conversation_id, started_at)"
            )

    @staticmethod
    def digest_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def create(
        self,
        *,
        operation: str,
        conversation_id: str | None,
        prompt: str,
        requested_parent_node_id: str | None = None,
    ) -> dict[str, Any]:
        operation_id = str(uuid.uuid4())
        now = time.time()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO mutation_attempts (
                    operation_id, operation, conversation_id, requested_parent_node_id,
                    prompt_digest, transport, state, started_at
                ) VALUES (?, ?, ?, ?, ?, 'dom', 'created', ?)
                """,
                (
                    operation_id,
                    operation,
                    conversation_id,
                    requested_parent_node_id,
                    self.digest_prompt(prompt),
                    now,
                ),
            )
        return self.get(operation_id) or {}

    def update(self, operation_id: str, *, state: str | None = None, **fields: Any) -> dict[str, Any]:
        allowed = {
            "conversation_id", "authenticated_at", "hydrated_at", "submitted_at",
            "committed_at", "completed_at", "user_node_id", "assistant_node_id",
            "error_class", "error_detail", "transport",
        }
        values: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed}
        if state is not None:
            values["state"] = state
        if not values:
            return self.get(operation_id) or {}
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), operation_id]
        with self.connection:
            self.connection.execute(
                f"UPDATE mutation_attempts SET {assignments} WHERE operation_id = ?",
                params,
            )
        return self.get(operation_id) or {}

    def mark_error(self, operation_id: str, state: str, exc: BaseException) -> dict[str, Any]:
        return self.update(
            operation_id,
            state=state,
            error_class=exc.__class__.__name__,
            error_detail=str(exc),
            completed_at=time.time() if state in TERMINAL_STATES else None,
        )

    def get(self, operation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM mutation_attempts WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM mutation_attempts ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]
