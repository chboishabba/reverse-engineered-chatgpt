"""Durable mutation-attempt state in the canonical re-gpt SQLite database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from .attachments import CanonicalAttachment, LocalAttachment
from .storage import DEFAULT_DB_PATH

TERMINAL_STATES = {"complete", "failed_precommit", "attachment_failed_precommit"}


class MutationOperationStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self.connection.execute("PRAGMA busy_timeout=30000;")
        self._init_schema()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()
    def close(self) -> None: self.connection.close()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute("""
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
                    attachment_ready_at REAL,
                    submitted_at REAL,
                    committed_at REAL,
                    completed_at REAL,
                    user_node_id TEXT,
                    assistant_node_id TEXT,
                    error_class TEXT,
                    error_detail TEXT
                )
            """)
            # Forward-compatible migration for PR38 databases created before attachments.
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(mutation_attempts)")}
            if "attachment_ready_at" not in columns:
                self.connection.execute("ALTER TABLE mutation_attempts ADD COLUMN attachment_ready_at REAL")
            for name, kind in (("last_activity_at", "REAL"), ("current_stage", "TEXT"), ("activity_summary", "TEXT")):
                if name not in columns:
                    self.connection.execute(f"ALTER TABLE mutation_attempts ADD COLUMN {name} {kind}")
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS mutation_activities (
                    operation_id TEXT NOT NULL, observed_at REAL NOT NULL,
                    stage TEXT NOT NULL, summary TEXT NOT NULL
                )
            """)
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_mutation_activities_operation ON mutation_activities(operation_id, observed_at)")
            if "conversation_id" in columns and "started_at" in columns:
                self.connection.execute("CREATE INDEX IF NOT EXISTS idx_mutation_attempts_conversation ON mutation_attempts(conversation_id, started_at)")
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS mutation_attachments (
                    operation_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    canonical_file_id TEXT,
                    canonical_pointer TEXT,
                    canonical_node_id TEXT,
                    PRIMARY KEY (operation_id, ordinal),
                    FOREIGN KEY (operation_id) REFERENCES mutation_attempts(operation_id) ON DELETE CASCADE
                )
            """)

    @staticmethod
    def digest_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def create(self, *, operation: str, conversation_id: str | None, prompt: str, requested_parent_node_id: str | None = None) -> dict[str, Any]:
        operation_id = str(uuid.uuid4())
        now = time.time()
        with self.connection:
            self.connection.execute("""
                INSERT INTO mutation_attempts (
                    operation_id, operation, conversation_id, requested_parent_node_id,
                    prompt_digest, transport, state, started_at
                ) VALUES (?, ?, ?, ?, ?, 'dom', 'created', ?)
            """, (operation_id, operation, conversation_id, requested_parent_node_id, self.digest_prompt(prompt), now))
        return self.get(operation_id) or {}

    def update(self, operation_id: str, *, state: str | None = None, **fields: Any) -> dict[str, Any]:
        allowed = {
            "conversation_id", "authenticated_at", "hydrated_at", "attachment_ready_at",
            "submitted_at", "committed_at", "completed_at", "user_node_id",
            "assistant_node_id", "error_class", "error_detail", "transport",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        if state is not None: values["state"] = state
        if not values: return self.get(operation_id) or {}
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connection:
            self.connection.execute(f"UPDATE mutation_attempts SET {assignments} WHERE operation_id = ?", [*values.values(), operation_id])
        return self.get(operation_id) or {}

    def record_activity(self, operation_id: str, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "stream")[:64]
        if kind not in {"thinking", "tool_invocation", "token_delta", "terminal"}:
            return
        stage = {"thinking": "reasoning", "tool_invocation": "tool_use",
                 "token_delta": "streaming", "terminal": "terminal"}[kind]
        if kind == "tool_invocation":
            recipient = str(event.get("recipient") or "tool")
            summary = recipient[:160]
        elif kind == "thinking":
            summary = str(event.get("content_type") or "thinking")[:160]
        elif kind == "token_delta":
            channel = str(event.get("channel") or "")
            summary = "thinking delta" if channel == "analysis" else "token delta received"
        else:
            summary = kind
        now = time.time()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE mutation_attempts SET last_activity_at=?, current_stage=?, activity_summary=? "
                "WHERE operation_id=?", (now, stage, summary, operation_id),
            )
            if cursor.rowcount:
                self.connection.execute(
                    "INSERT INTO mutation_activities VALUES (?,?,?,?)",
                    (operation_id, now, stage, summary),
                )

    def live_activity(self, operation_id: str) -> dict[str, Any] | None:
        row = self.get(operation_id)
        if row is None:
            return None
        recent = self.connection.execute(
            "SELECT observed_at,stage,summary FROM mutation_activities "
            "WHERE operation_id=? ORDER BY observed_at DESC LIMIT 50",
            (operation_id,),
        ).fetchall()
        last = row.get("last_activity_at")
        return {
            "operation_id": operation_id,
            "last_activity_at": last,
            "seconds_since_last_activity": max(0.0, time.time() - last) if last is not None else None,
            "current_stage": row.get("current_stage"),
            "activity_summary": row.get("activity_summary"),
            "recent_activities": [dict(item) for item in recent],
        }

    def fleet_telemetry(self, window_seconds: float = 120.0, limit: int = 50) -> dict[str, Any]:
        since = time.time() - window_seconds
        with self.connection:
            cur = self.connection.cursor()
            cur.execute(
                "SELECT count(DISTINCT operation_id) FROM mutation_activities WHERE observed_at > ?",
                (since,),
            )
            count = cur.fetchone()[0]
            cur.execute(
                "SELECT m.operation_id, m.observed_at, m.stage, m.summary "
                "FROM mutation_activities m JOIN ("
                "  SELECT operation_id, max(rowid) as max_r FROM mutation_activities WHERE observed_at > ? GROUP BY operation_id"
                ") sub ON m.operation_id = sub.operation_id AND m.rowid = sub.max_r "
                "ORDER BY m.rowid DESC LIMIT ?",
                (since, limit),
            )
            rows = [
                {
                    "operation_id": r[0],
                    "observed_at": r[1],
                    "stage": r[2],
                    "summary": r[3],
                    "seconds_ago": round(max(0.0, time.time() - r[1]), 1),
                }
                for r in cur.fetchall()
            ]
        return {
            "window_seconds": window_seconds,
            "active_operations_count": count,
            "recent_operations": rows,
        }

    def record_local_attachments(self, operation_id: str, attachments: Sequence[LocalAttachment]) -> None:
        with self.connection:
            for index, item in enumerate(attachments):
                self.connection.execute("""
                    INSERT OR REPLACE INTO mutation_attachments (
                        operation_id, ordinal, source_path, filename, mime_type,
                        size_bytes, sha256, canonical_file_id, canonical_pointer, canonical_node_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """, (operation_id, index, str(item.path), item.filename, item.mime_type, item.size, item.sha256))

    def record_canonical_attachments(self, operation_id: str, attachments: Sequence[CanonicalAttachment]) -> None:
        with self.connection:
            for index, item in enumerate(attachments):
                self.connection.execute("""
                    UPDATE mutation_attachments
                    SET canonical_file_id = ?, canonical_pointer = ?, canonical_node_id = ?
                    WHERE operation_id = ? AND ordinal = ?
                """, (item.file_id or None, item.pointer, item.node_id, operation_id, index))

    def attachment_rows(self, operation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM mutation_attachments WHERE operation_id = ? ORDER BY ordinal", (operation_id,)).fetchall()
        return [dict(row) for row in rows]

    def mark_error(self, operation_id: str, state: str, exc: BaseException) -> dict[str, Any]:
        observations = getattr(exc, "observations", ())
        preflight: list[dict[str, Any]] = []
        for observation in observations if isinstance(observations, Sequence) else ():
            to_dict = getattr(observation, "to_dict", None)
            raw = to_dict() if callable(to_dict) else observation
            if not isinstance(raw, dict):
                continue
            preflight.append({
                "stage": raw.get("bootstrap_stage"),
                "status": raw.get("status"),
                "response_class": raw.get("response_class"),
            })
        detail = str(exc)
        if preflight:
            detail = f"{detail}; redacted_preflight={json.dumps(preflight, sort_keys=True, separators=(',', ':'))}"
        return self.update(
            operation_id, state=state, error_class=exc.__class__.__name__, error_detail=detail,
            completed_at=time.time() if state in TERMINAL_STATES else None,
        )

    def get(self, operation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM mutation_attempts WHERE operation_id = ?", (operation_id,)).fetchone()
        if not row: return None
        result = dict(row)
        result["attachments"] = self.attachment_rows(operation_id)
        return result

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT operation_id FROM mutation_attempts ORDER BY started_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self.get(str(row[0])) for row in rows if self.get(str(row[0])) is not None]
