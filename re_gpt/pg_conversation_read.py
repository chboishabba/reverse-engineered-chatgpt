"""Read-only canonical conversation catalog from ITIR PostgreSQL corpus.

This is a projection of existing corpus.chat_archive_message, not a new store.
Network hydration is a separate operation. SLR remains semantic runtime owner.
"""
from __future__ import annotations

import os


class CanonicalHistoryUnavailable(RuntimeError):
    pass


def _connect():
    dsn = os.environ.get("ITIR_PG_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise CanonicalHistoryUnavailable("Configure ITIR_PG_DSN for canonical history")
    try:
        import psycopg
    except ImportError as exc:
        raise CanonicalHistoryUnavailable("psycopg is required for canonical PG reads") from exc
    try:
        return psycopg.connect(dsn, connect_timeout=3)
    except Exception as exc:
        raise CanonicalHistoryUnavailable("Canonical PostgreSQL connection unavailable") from exc


def list_pg_conversations(*, query: str = "", limit: int = 100, offset: int = 0,
                          connect=None):
    """Return conversation IDs/titles from verified stored message records.

    SQL binds all user input. No network fetch, implicit hydration, mutation,
    local SQLite fallback, or promotion from candidate evidence.
    """
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("invalid search text")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid limit")
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError("invalid offset")
    connection = (connect or _connect)()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT substring(conversation_ref from 14) AS id,
                       (array_agg(thread_title ORDER BY message_time_ref DESC))[1] AS title,
                       max(message_time_ref) AS updated,
                       count(*) AS message_count
                FROM corpus.chat_archive_message
                WHERE identity_verified IS TRUE
                  AND conversation_ref LIKE %s
                  AND (%s = '' OR thread_title ILIKE %s
                       OR conversation_ref ILIKE %s)
                GROUP BY conversation_ref
                ORDER BY updated DESC
                LIMIT %s OFFSET %s
                """, ("conversation:%", query, "%" + query + "%",
                      "%" + query + "%", limit, offset))
            rows = cursor.fetchall()
        return [{"id": str(r[0]), "title": str(r[1] or "Untitled"),
                 "update_time": str(r[2] or ""), "message_count": int(r[3])}
                for r in rows]
    except Exception as exc:
        raise CanonicalHistoryUnavailable("PostgreSQL canonical conversation projection failed") from exc
    finally:
        connection.close()


def list_synced_roster(*, query="", limit=100, connect=None):
    """Picker projection combining hydrated corpus and roster-only identities."""
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("invalid query")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid limit")
    conn = (connect or _connect)()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.conversation_id, r.title, r.remote_updated, r.state
                FROM ops.chat_hydration_roster r
                WHERE (%s='' OR r.title ILIKE %s OR r.conversation_id ILIKE %s)
                ORDER BY r.remote_updated DESC NULLS LAST, r.observed_at DESC
                LIMIT %s
            """, (query, '%' + query + '%', '%' + query + '%', limit))
            rows = cur.fetchall()
        known = [{"id": str(r[0]), "title": str(r[1]), "update_time": str(r[2] or ""),
                  "hydration_state": str(r[3])} for r in rows]
    finally:
        conn.close()
    if len(known) < limit:
        existing = {r["id"] for r in known}
        for item in list_pg_conversations(query=query, limit=limit, connect=connect):
            if item["id"] not in existing:
                known.append(item)
                existing.add(item["id"])
                if len(known) >= limit:
                    break
    return known


def read_pg_conversation(conversation_id: str, *, connect=None, limit: int = 10000):
    """Display original stored message bodies and provenance, without writes."""
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 150:
        raise ValueError("invalid conversation ID")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("invalid limit")
    conn = (connect or _connect)()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT node_ref, role_ref, body_storage_ref, thread_title,
                       message_ref, parent_node_ref, branch_membership_ref,
                       message_time_ref
                FROM corpus.chat_archive_message
                WHERE conversation_ref=%s AND identity_verified IS TRUE
                ORDER BY message_time_ref ASC, message_ref ASC
                LIMIT %s
                """, ("conversation:" + conversation_id, limit))
            rows = cur.fetchall()
        if not rows:
            raise KeyError("conversation not found in canonical PostgreSQL")
        return {"conversation_id": conversation_id, "title": str(rows[-1][3] or "Untitled"),
                "source": "itir_postgresql_corpus",
                "messages": [{"id": str(r[0]), "role": str(r[1]), "text": str(r[2] or ""),
                              "message_ref": str(r[4]), "parent_node_ref": r[5],
                              "branch": r[6], "message_time": str(r[7] or "")}
                             for r in rows]}
    except KeyError:
        raise
    except Exception as exc:
        raise CanonicalHistoryUnavailable("PostgreSQL conversation read failed") from exc
    finally:
        conn.close()
