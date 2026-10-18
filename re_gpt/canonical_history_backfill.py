"""One-way, resumable legacy re-gpt history → canonical structurer backfill.

No network, mutation, browser or PNF. Legacy flat rows cannot reconstruct
unknown graph parents, branches or attachment pointers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .canonical_archive_hydrator import (
    _digest, _identity, _load_puller, _receipt_db, canonical_archive_path,
)
from .storage import DEFAULT_DB_PATH

def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in db.execute(f"PRAGMA table_info({table})")}

def _canonical_existing(db: sqlite3.Connection, account: str) -> dict[str, str]:
    """Read existing canonical identities, including other producer ingest paths.

    No guessing if an older schema cannot expose message IDs and content.
    """
    cols = _table_columns(db, "messages")
    required = {"platform", "account_id", "source_thread_id",
                "source_message_id", "node_id", "content"}
    if not required <= cols:
        raise RuntimeError("canonical messages identity/content schema not verified")
    result = {}
    for thread, msg, node, content in db.execute(
        """SELECT source_thread_id,source_message_id,node_id,content
           FROM messages WHERE platform=? AND account_id=?""", ("chatgpt", account)
    ):
        if thread is None or msg is None or node is None or content is None:
            continue
        identity = "|".join(("chatgpt", account, str(thread), str(msg), str(node)))
        result[identity] = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
    return result

def _row_to_structurer(row: sqlite3.Row, title: str, thread: str) -> dict[str, Any]:
    # Flat history lacks verified node/parent/branch metadata.
    message_id = str(row["message_key"] or f"legacy-index:{row['message_index']}").strip()
    if not message_id:
        raise ValueError("empty stable message identity")
    created_at = row["create_time"]
    return {
        "thread_id": thread, "thread_title": title,
        "source_message_id": message_id, "node_id": message_id,
        "parent_node_id": "", "branch_membership": "unknown",
        "role": str(row["author"]), "content": str(row["content"]),
        "created_at": float(created_at) if created_at is not None else 0.0,
        "body_storage_ref": message_id,
        "provenance_json": json.dumps({
            "source": "regpt_legacy_history", "graph_parent_verified": False,
            "graph_branch_verified": False, "attachment_refs_verified": False,
            "identity_kind": "message_key" if row["message_key"] else "legacy_index",
            "source_thread_id": thread,
        }, sort_keys=True),
    }

def backfill_history(
    *, history_db: str | Path = DEFAULT_DB_PATH,
    archive_db: str | Path | None = None,
    account: str | None = None, batch_size: int = 250,
    producer: Any = None,
) -> dict[str, int]:
    account = (account or os.environ.get("ITIR_CHATGPT_ACCOUNT") or "").strip()
    if not account:
        raise ValueError("explicit --account or ITIR_CHATGPT_ACCOUNT required")
    if not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be 1..10000")
    source = Path(history_db).expanduser()
    archive = canonical_archive_path(archive_db)
    if not source.is_file() or not archive.is_file():
        raise FileNotFoundError("explicit existing history and canonical archive required")
    if source.resolve() == archive.resolve():
        raise ValueError("history and canonical archive must be distinct")
    puller = producer or _load_puller()
    ingest = puller._load_structurer_ingest()
    counts = dict(conversations_scanned=0, messages_ingested=0,
                  replayed=0, conflicts=0, skipped_as_already_present=0)
    # History is opened read-only: never migrate or modify operational tables.
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as history, \
         sqlite3.connect(archive) as canonical:
        history.row_factory = sqlite3.Row
        _receipt_db(canonical)
        canonical.commit()
        present = _canonical_existing(canonical, account)
        receipts = {r[0]: r[1] for r in canonical.execute(
            "SELECT identity,body_sha256 FROM graph_archive_hydration_receipt")}
        cols = _table_columns(history, "messages")
        required = {"conversation_id", "message_index", "author",
                    "content", "create_time", "message_key"}
        if not required <= cols:
            raise RuntimeError("legacy history messages schema mismatch")
        titles = {str(r[0]): str(r[1] or "") for r in history.execute(
            "SELECT conversation_id,title FROM conversations")}
        cursor = history.execute("""SELECT conversation_id,message_index,author,content,
            create_time,message_key FROM messages
            ORDER BY conversation_id,message_index,author""")
        last_thread = None
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            incoming = []
            for raw in batch:
                thread = str(raw["conversation_id"])
                if thread != last_thread:
                    counts["conversations_scanned"] += 1
                    last_thread = thread
                row = _row_to_structurer(raw, titles.get(thread, ""), thread)
                identity, digest = _identity(row, account), _digest(row)
                previous = receipts.get(identity)
                canonical_digest = present.get(identity)
                if previous == digest:
                    counts["replayed"] += 1
                    continue
                if previous is not None and previous != digest or (
                    canonical_digest is not None and canonical_digest != digest
                ):
                    old = previous if previous is not None else canonical_digest
                    ref = hashlib.sha256((identity+"\n"+old+"\n"+digest).encode()).hexdigest()
                    canonical.execute("""INSERT OR IGNORE INTO graph_archive_hydration_conflict
                        (receipt_ref,identity,prior_sha256,proposed_sha256)
                        VALUES (?,?,?,?)""", (ref, identity, old, digest))
                    counts["conflicts"] += 1
                    continue
                if canonical_digest == digest:
                    canonical.execute("""INSERT OR IGNORE INTO graph_archive_hydration_receipt
                        (identity,body_sha256,thread_id,node_id,archive_locator,source_ref)
                        VALUES (?,?,?,?,?,?)""", (
                        identity,digest,thread,row["node_id"],
                        "sqlite:"+str(archive),row["source_message_id"]))
                    receipts[identity] = digest
                    counts["skipped_as_already_present"] += 1
                    continue
                incoming.append((identity,digest,row))
            canonical.commit()
            if incoming:
                stats = ingest.ingest_parsed_messages(
                    [row for _,_,row in incoming], db_path=str(archive),
                    platform="chatgpt", account_id=account,
                    source_id="history-backfill:v1", upsert_empty_text=False, debug=False)
                inserted = int(stats.get("inserted",0))
                duplicates = int(stats.get("duplicates",0))
                if inserted + duplicates != len(incoming):
                    raise RuntimeError("producer ingest did not account for every candidate")
                # Recheck canonical materialisation before marking success.
                present = _canonical_existing(canonical, account)
                for identity,digest,row in incoming:
                    if present.get(identity) != digest:
                        raise RuntimeError("producer identity/digest verification failed")
                    canonical.execute("""INSERT OR IGNORE INTO graph_archive_hydration_receipt
                        (identity,body_sha256,thread_id,node_id,archive_locator,source_ref)
                        VALUES (?,?,?,?,?,?)""", (
                        identity,digest,row["thread_id"],row["node_id"],
                        "sqlite:"+str(archive),row["source_message_id"]))
                    receipts[identity] = digest
                counts["messages_ingested"] += inserted
                counts["skipped_as_already_present"] += duplicates
            canonical.commit()
    return counts

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--archive-db")
    parser.add_argument("--account")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args(argv)
    print(json.dumps(backfill_history(history_db=args.history_db,
        archive_db=args.archive_db, account=args.account,
        batch_size=args.batch_size), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
