"""Verified graph → canonical structurer incremental hydration.

No AO reads from the re-gpt cache. The only message body writes use the
existing chat-export-structurer ingest producer.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

DEFAULT_ARCHIVE = Path.home() / "chat_archive.sqlite"

def canonical_archive_path(explicit: str | Path | None = None) -> Path:
    value = explicit if explicit is not None else os.environ.get("ITIR_CANONICAL_CHAT_ARCHIVE")
    result = Path(value).expanduser() if value is not None else DEFAULT_ARCHIVE
    if not str(result):
        raise ValueError("canonical archive path is empty")
    return result

def _load_puller():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pull_to_structurer.py"
    spec = importlib.util.spec_from_file_location("regpt_structurer_puller", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("structurer puller unavailable")
    import sys
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def _digest(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()

def _identity(row: Mapping[str, Any], account: str) -> str:
    return "|".join(("chatgpt", account, str(row["thread_id"]),
                     str(row["source_message_id"]), str(row["node_id"])))

def _receipt_db(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS graph_archive_hydration_receipt (
        identity TEXT PRIMARY KEY, body_sha256 TEXT NOT NULL,
        thread_id TEXT NOT NULL, node_id TEXT NOT NULL,
        archive_locator TEXT NOT NULL, source_ref TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS graph_archive_hydration_conflict (
        receipt_ref TEXT PRIMARY KEY, identity TEXT NOT NULL,
        prior_sha256 TEXT NOT NULL, proposed_sha256 TEXT NOT NULL)""")

def hydrate_verified_graph(
    graph: Mapping[str, Any], *, conversation_id: str, account: str,
    archive_db: str | Path | None = None,
    producer: Any = None, parser: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    if not account.strip() or not conversation_id.strip():
        raise ValueError("explicit account and canonical conversation identity required")
    actual = str(graph.get("conversation_id") or graph.get("id") or "")
    if actual != conversation_id:
        raise ValueError("graph identity mismatch: refusing hydration")
    puller = producer or _load_puller()
    extractor = parser or puller._load_chatgpt_parser()
    messages = puller._extract_messages_for_ingest(dict(graph), extract_text=extractor)
    archive = canonical_archive_path(archive_db)
    if not archive.is_file():
        raise FileNotFoundError(f"canonical archive missing: {archive}")
    incoming = []
    conflict_count = replayed = 0
    # Receipts reside in the canonical archive, never in a second corpus store.
    with sqlite3.connect(archive) as db:
        _receipt_db(db)
        db.commit()
        for row in messages:
            row = dict(row)
            row["thread_id"] = conversation_id
            identity = _identity(row, account)
            digest = _digest(row)
            old = db.execute("SELECT body_sha256 FROM graph_archive_hydration_receipt WHERE identity=?",
                             (identity,)).fetchone()
            if old:
                if old[0] == digest:
                    replayed += 1
                else:
                    conflict_ref = hashlib.sha256(
                        (identity+"\n"+old[0]+"\n"+digest).encode()).hexdigest()
                    db.execute("""INSERT OR IGNORE INTO graph_archive_hydration_conflict
                        (receipt_ref,identity,prior_sha256,proposed_sha256)
                        VALUES (?,?,?,?)""",(conflict_ref,identity,old[0],digest))
                    conflict_count += 1
                continue
            row["provenance_json"] = json.dumps({"source_platform":"chatgpt",
                "source_account":account,"source_thread_id":conversation_id,
                "node_id":row["node_id"],"parent_node_id":row["parent_node_id"],
                "branch_membership":row["branch_membership"],
                "content_sha256":digest},sort_keys=True)
            incoming.append((identity,digest,row))
        # Commit conflict receipts and release the write lock before producer
        # opens its own canonical archive connection.
        db.commit()
        # Producer ingest is authoritative. Only mark receipts after it succeeds.
        stats = {"inserted":0,"duplicates":0}
        if incoming:
            stats = puller._load_structurer_ingest().ingest_parsed_messages(
                [r for _,_,r in incoming], db_path=str(archive),
                platform="chatgpt",account_id=account,
                source_id="graph-reconcile:"+conversation_id,
                upsert_empty_text=False,debug=False)
            for identity,digest,row in incoming:
                db.execute("""INSERT OR IGNORE INTO graph_archive_hydration_receipt
                    (identity,body_sha256,thread_id,node_id,archive_locator,source_ref)
                    VALUES (?,?,?,?,?,?)""",
                    (identity,digest,conversation_id,row["node_id"],
                     "sqlite:"+str(archive),row["source_message_id"]))
        db.commit()
    return {"schema":"re-gpt.canonical-graph-hydration.v1",
        "archive_db":str(archive),"source_thread_id":conversation_id,
        "selected":len(messages),"new_identities":len(incoming),
        "replayed":replayed,"conflicts":conflict_count,
        "ingest_inserted":int(stats.get("inserted",0)),
        "ingest_duplicates":int(stats.get("duplicates",0)),
        "candidate_only":True,"authoritative_corpus":"structurer"}
