#!/usr/bin/env python3
"""Export exact-coordinate archived ChatGPT messages for SensibLaw/SLR.

This exporter reads the canonical local SQLite archive only. It never reaches
ChatGPT. It fails closed unless the archive preserves the original
conversation/message/node/branch coordinates needed by the downstream source
membrane.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = "sensiblaw.chat-archive-message.v0_1"

REQUIRED_COLUMNS = {
    "platform",
    "account_id",
    "ts",
    "role",
    "text",
    "title",
    "source_thread_id",
    "source_message_id",
    "node_id",
    "parent_node_id",
    "branch_membership",
    "identity_verified",
}

OPTIONAL_COLUMNS = {
    "tool_kind",
    "citation_token",
    "filename",
    "asset_pointer",
    "source_scope",
    "body_storage_ref",
    "mime_type",
    "provenance_json",
    "source_id",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=Path.home() / "chat_archive.sqlite")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--conversation-id")
    p.add_argument("--account", default="main")
    p.add_argument("--include-inactive", action="store_true")
    return p.parse_args()


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def normalized_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit(f"archive database does not exist: {args.db}")

    con = sqlite3.connect(str(args.db))
    con.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "messages" not in tables:
            raise SystemExit("archive has no messages table")

        columns = table_columns(con, "messages")
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise SystemExit(
                "archive is not exact-coordinate SLR-ready; missing messages columns: "
                + ", ".join(missing)
                + ". Re-ingest through the enriched structurer path rather than "
                  "substituting canonical message/thread hashes."
            )

        selected_optional = sorted(OPTIONAL_COLUMNS & columns)
        select_columns = sorted(REQUIRED_COLUMNS) + selected_optional
        where = ["platform = ?", "account_id = ?"]
        params: list[Any] = ["chatgpt", args.account]
        if args.conversation_id:
            where.append("source_thread_id = ?")
            params.append(args.conversation_id)
        if not args.include_inactive:
            where.append("branch_membership = 'active'")

        sql = (
            "SELECT "
            + ", ".join(select_columns)
            + " FROM messages WHERE "
            + " AND ".join(where)
            + " ORDER BY ts, source_message_id, node_id"
        )
        rows = list(con.execute(sql, params))
    finally:
        con.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    exported = 0
    with args.output.open("w", encoding="utf-8") as out:
        for row in rows:
            record = {column: row[column] for column in select_columns}
            if not normalized_bool(record.get("identity_verified")):
                raise SystemExit(
                    "refusing to export unverified payload identity for "
                    f"{record.get('source_thread_id')} / {record.get('source_message_id')}"
                )
            for required in (
                "source_thread_id",
                "source_message_id",
                "node_id",
                "branch_membership",
                "ts",
                "role",
            ):
                if not str(record.get(required) or "").strip():
                    raise SystemExit(
                        f"required archived coordinate is empty: {required}"
                    )
            branch = str(record["branch_membership"]).strip().lower()
            if branch not in {"active", "inactive"}:
                raise SystemExit(
                    f"unknown branch_membership {record['branch_membership']!r}"
                )

            payload = {
                "schema": SCHEMA,
                "conversation_ref": str(record["source_thread_id"]),
                "message_ref": str(record["source_message_id"]),
                "node_ref": str(record["node_id"]),
                "parent_node_ref": str(record.get("parent_node_id") or ""),
                "branch_membership": branch,
                "message_time_ref": str(record["ts"]),
                "role_ref": str(record["role"]),
                "thread_title": str(record.get("title") or ""),
                "content": str(record.get("text") or ""),
                "tool_kind": str(record.get("tool_kind") or ""),
                "citation_token": str(record.get("citation_token") or ""),
                "filename": str(record.get("filename") or ""),
                "asset_pointer": str(record.get("asset_pointer") or ""),
                "source_scope": str(record.get("source_scope") or ""),
                "body_storage_ref": str(record.get("body_storage_ref") or ""),
                "mime_type": str(record.get("mime_type") or ""),
                "archive_source_id": str(record.get("source_id") or ""),
                "provenance_json": str(record.get("provenance_json") or ""),
                "identity_verified": True,
                "candidate_only": True,
                "semantic_promotion": False,
            }
            out.write(json.dumps(payload, sort_keys=True) + "\n")
            exported += 1

    print(
        "CHAT_ARCHIVE_SLR_EXPORT "
        f"schema={SCHEMA} rows={exported} "
        "identity_verified=true candidate_only=true semantic_promotion=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
