"""Explicit network-to-PostgreSQL hydration with a persistent PG work queue.

No SQLite writes, detached daemon, TUI dependency, or automatic mutation retry.
Only verified graph reads are imported. Uses the existing ITIR corpus hierarchy.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
from typing import Any

from .pg_conversation_read import _connect, list_pg_conversations
from .graph_broker import fetch_exact_graph


def ensure_roster_table(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ops.chat_hydration_roster (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                remote_updated TEXT,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                hydrated_at TIMESTAMPTZ,
                state TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)


def record_roster(items, *, connect=_connect):
    """Commit remote identities into PG without claiming message hydration."""
    conn = connect()
    try:
        with conn.transaction():
            ensure_roster_table(conn)
            with conn.cursor() as cur:
                for item in items:
                    cid = item.get("id")
                    if not isinstance(cid, str) or not cid or len(cid) > 150:
                        continue
                    cur.execute("""
                        INSERT INTO ops.chat_hydration_roster
                          (conversation_id, title, remote_updated, state)
                        VALUES (%s,%s,%s,'queued')
                        ON CONFLICT (conversation_id) DO UPDATE SET
                          title=EXCLUDED.title,
                          remote_updated=EXCLUDED.remote_updated,
                          observed_at=now(),
                          state=CASE
                            WHEN ops.chat_hydration_roster.remote_updated
                                 IS DISTINCT FROM EXCLUDED.remote_updated
                            THEN 'queued'
                            ELSE ops.chat_hydration_roster.state
                          END
                    """, (cid, str(item.get("title") or "Untitled"),
                          str(item.get("update_time") or "")))
    finally:
        conn.close()


def roster_page(*, page_size=100, pages=1, connect=_connect, client_factory=None):
    """Observe remote roster into PG, returning only committed identities."""
    if not (1 <= page_size <= 100 and 1 <= pages <= 20):
        raise ValueError("invalid roster bounds")
    if client_factory is None:
        from .sync_chatgpt import SyncChatGPT
        from .utils import get_session_token
        client_factory = lambda: SyncChatGPT(session_token=get_session_token())
    items = []
    seen = set()
    with client_factory() as client:
        for index in range(pages):
            response = client.list_conversations_page(offset=index * page_size, limit=page_size)
            if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                raise ValueError("unexpected roster response")
            raw = response["items"]
            for item in raw:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] not in seen:
                    seen.add(item["id"])
                    items.append(item)
            if len(raw) < page_size:
                break
    record_roster(items, connect=connect)
    return {"observed": len(items), "persisted_to": "postgresql",
            "hydrated": 0}


def _persist_graph(conn, cid: str, graph: dict[str, Any]):
    """Persist full original text via ITIR's five-level content-addressed schema."""
    mapping = graph.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("noncanonical or empty graph")
    active_nodes = set()
    current = graph.get("current_node")
    while isinstance(current, str) and current in mapping and current not in active_nodes:
        active_nodes.add(current)
        current = mapping[current].get("parent") if isinstance(mapping[current], dict) else None
    count = 0
    with conn.cursor() as cur:
        for node_key, node in mapping.items():
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            role = str((msg.get("author") or {}).get("role") or "")
            content = msg.get("content") or {}
            raw_parts = content.get("parts") or []
            parts = [p for p in raw_parts if isinstance(p, str)]
            if role not in {"user", "assistant", "tool", "system"} or not parts:
                continue
            body = "".join(parts)
            node_id = str(msg.get("id") or node_key)
            parent = node.get("parent")
            if not node_id or not body:
                continue
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            binary = "source-content:" + digest
            canonical = "canonical-content:" + digest + ":media-normalization:utf8:v0_1"
            doc = "document:" + digest
            span = "mention:document:" + digest + ":0:" + str(len(body))
            message_ref = "message:" + cid + ":" + node_id
            # Existing identity with different source is a revision conflict, not
            # a successful hydration. Do not silently overwrite canonical text.
            cur.execute("SELECT source_revision_ref FROM corpus.chat_archive_message WHERE message_ref=%s",
                        (message_ref,))
            existing = cur.fetchone()
            if existing and existing[0] != "source:sha256:" + digest:
                raise ValueError("canonical message revision conflict: " + message_ref)
            created = msg.get("create_time")
            try:
                ts = datetime.fromtimestamp(float(created), timezone.utc).isoformat() if created else "1970-01-01T00:00:00+00:00"
            except (ValueError, TypeError, OverflowError):
                ts = "1970-01-01T00:00:00+00:00"
            cur.execute("""INSERT INTO corpus.binary_content
                (content_ref,content_sha256,media_type,compression_ref,payload,uncompressed_byte_length)
                VALUES (%s,%s,'text/plain',NULL,%s,%s)
                ON CONFLICT (content_ref) DO NOTHING""",
                (binary, bytes.fromhex(digest), body.encode("utf-8"), len(body.encode("utf-8"))))
            cur.execute("""INSERT INTO corpus.canonical_content
                (canonical_ref,content_sha256,encoding_ref,normalization_ref,compression_ref,payload,uncompressed_byte_length)
                VALUES (%s,%s,'utf-8','media-normalization:utf8:v0_1',NULL,%s,%s)
                ON CONFLICT (canonical_ref) DO NOTHING""",
                (canonical, bytes.fromhex(digest), body.encode("utf-8"), len(body.encode("utf-8"))))
            cur.execute("""INSERT INTO corpus.document
                (document_ref,source_content_ref,canonical_ref,media_type,adapter_ref,
                 adapter_version,compiler_context_ref,document_sha256)
                VALUES (%s,%s,%s,'text/plain','media:utf8-text:v0_1',
                        'media-normalization:utf8:v0_1','compiler-context:local-only:v0_1',%s)
                ON CONFLICT (document_ref) DO NOTHING""",
                (doc,binary,canonical,bytes.fromhex(digest)))
            cur.execute("""INSERT INTO corpus.span
                (span_ref,document_ref,start_char,end_char,start_token,end_token,span_type_ref)
                VALUES (%s,%s,0,%s,0,1,'full_message') ON CONFLICT (span_ref) DO NOTHING""",
                (span,doc,len(body)))
            cur.execute("""INSERT INTO corpus.chat_archive_message
                (message_ref,conversation_ref,node_ref,parent_node_ref,
                 branch_membership_ref,message_time_ref,role_ref,thread_title,
                 content_kind_ref,source_scope,body_storage_ref,mime_type,
                 document_ref,source_revision_ref,full_message_span_ref,
                 identity_verified,candidate_only,creates_semantic_authority,
                 applicability_promoted,claim_truth_promoted)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'message','remote-hydration',
                        %s,'text/plain',%s,%s,%s,true,true,false,false,false)
                ON CONFLICT (message_ref) DO NOTHING""",
                (message_ref,"conversation:"+cid,"node:"+node_id,
                 "node:"+str(parent) if parent else None,
                 "active" if node_key in active_nodes else "inactive",ts,role,
                 str(graph.get("title") or "Untitled"),body,doc,
                 "source:sha256:"+digest,span))
            count += 1
    return count


def hydrate_one(cid: str, *, connect=_connect, fetcher=fetch_exact_graph):
    """Prioritised exact read, atomic canonical ingest, no SQLite fallback."""
    if not isinstance(cid, str) or not cid or len(cid) > 150:
        raise ValueError("invalid conversation id")
    result = fetcher(cid, adapter_order=("async", "sync"))
    graph = result.graph
    conn = connect()
    try:
        with conn.transaction():
            ensure_roster_table(conn)
            count = _persist_graph(conn, cid, graph)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops.chat_hydration_roster
                    (conversation_id,title,state,hydrated_at,attempt_count,last_error)
                    VALUES (%s,%s,'hydrated',now(),1,NULL)
                    ON CONFLICT (conversation_id) DO UPDATE SET
                    title=EXCLUDED.title,state='hydrated',hydrated_at=now(),
                    attempt_count=ops.chat_hydration_roster.attempt_count+1,
                    last_error=NULL""", (cid,str(graph.get("title") or "Untitled")))
        return {"conversation_id": cid, "messages": count, "state": "hydrated",
                "source": "postgresql"}
    finally:
        conn.close()


def hydrate_pending(*, limit=5, connect=_connect, fetcher=fetch_exact_graph):
    """Bounded explicit worker pass; safe to invoke repeatedly via MCP/AO."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("invalid batch limit")
    conn = connect()
    try:
        with conn.transaction():
            ensure_roster_table(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT conversation_id FROM ops.chat_hydration_roster
                WHERE state='queued' ORDER BY observed_at DESC
                LIMIT %s""", (limit,))
            ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    results = []
    for cid in ids:
        try:
            results.append(hydrate_one(cid, connect=connect, fetcher=fetcher))
        except Exception as exc:
            failed = connect()
            try:
                with failed.transaction():
                    with failed.cursor() as cur:
                        cur.execute("UPDATE ops.chat_hydration_roster SET state='error', attempt_count=attempt_count+1, last_error=%s WHERE conversation_id=%s", (type(exc).__name__, cid))
            finally:
                failed.close()
            results.append({"conversation_id": cid, "state": "error",
                            "error_class": type(exc).__name__})
    return {"attempted": len(results), "results": results}
