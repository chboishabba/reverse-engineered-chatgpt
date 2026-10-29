"""Schema-exact PostgreSQL registration of archival nodes and candidate PNF factors.

Caller owns the transaction. No commits, SQLite or binary sidecar files.
Input nodes must contain canonical graph IDs, not legacy flat message indices.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Iterable, Mapping

from .semantic_rejuvenation import Factor

def _digest(*values: object) -> str:
    return sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()

def _required(turn: Mapping[str, object], key: str) -> str:
    value = turn.get(key)
    if value is None or str(value) == "":
        raise ValueError(f"missing canonical thread coordinate: {key}")
    return str(value)

def register_thread_factors_and_messages(conn, conversation_id: str, title: str,
                                         turns: Iterable[Mapping[str, object]],
                                         factors: Iterable[Factor]) -> dict[str, int]:
    """Atomic registration; rollback on *any* exception.

    psycopg connections are transaction context managers. Do not pass an
    autocommit connection. No semantic authority is promoted.
    """
    if getattr(conn, "autocommit", False):
        raise ValueError("autocommit connections cannot guarantee registration atomicity")
    turns = list(turns)
    factors = list(factors)
    rows = []
    for turn in turns:
        node = _required(turn, "node_id")
        parent = turn.get("parent_node_id")
        timestamp = _required(turn, "message_time_ref")
        role = _required(turn, "author")
        if role not in {"user", "assistant", "tool", "system"}:
            raise ValueError(f"unsupported role: {role}")
        body = str(turn.get("content") or "")
        document = "document:sha256:" + _digest(conversation_id, node, body)
        rows.append((
            f"message:{conversation_id}:{node}", f"conversation:{conversation_id}",
            f"node:{node}", f"node:{parent}" if parent else None,
            str(turn.get("branch_membership_ref") or "active"), timestamp,
            role, title, "message", body, "text/plain",
            "chatgpt-archive", document,
            str(turn.get("source_revision_ref") or "source:sha256:" + _digest(conversation_id, node, body)),
            str(turn.get("full_message_span_ref") or "span:sha256:" + _digest(node, body)),
        ))
    by_index = {int(t["message_index"]): t for t in turns if "message_index" in t}
    factor_rows = []
    for factor in factors:
        turn = by_index.get(factor.source_turn)
        if turn is None:
            raise ValueError(f"factor source turn absent: {factor.source_turn}")
        node = _required(turn, "node_id")
        body = str(turn.get("content") or "")
        statement = f"statement:{conversation_id}:{node}"
        batch = "batch:sha256:" + _digest(conversation_id)
        # Character spans are exact surface offsets, not guessed token positions.
        cursor = 0
        for role, surface in (("subj", factor.subject), ("pred", factor.predicate), ("obj", factor.object)):
            start = body.find(surface, cursor)
            if start < 0:
                raise ValueError(f"factor surface absent from source node {node}: {surface}")
            end = start + len(surface)
            factor_rows.append((
                "candidate:sha256:" + _digest(statement, factor.digest, role, start, end),
                batch, statement, role, start, end, surface, surface.casefold(),
                f"message:{conversation_id}:{node}", True,
            ))
            cursor = end
    with conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute("""
                    INSERT INTO corpus.chat_archive_message
                    (message_ref,conversation_ref,node_ref,parent_node_ref,
                     branch_membership_ref,message_time_ref,role_ref,thread_title,
                     content_kind_ref,body_storage_ref,mime_type,source_scope,
                     document_ref,source_revision_ref,full_message_span_ref,
                     identity_verified,candidate_only,creates_semantic_authority,
                     applicability_promoted,claim_truth_promoted)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            true,true,false,false,false)
                    ON CONFLICT (message_ref) DO NOTHING
                """, row)
            for row in factor_rows:
                cur.execute("""
                    INSERT INTO pnf.statement_candidate_factor
                    (candidate_ref,batch_ref,statement_ref,role_ref,
                     source_start_char,source_end_char,surface,lemma,
                     dependency_ref,candidate_only)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
                    ON CONFLICT (candidate_ref) DO NOTHING
                """, row[:-1])
            if factor_rows:
                product_ref = "product:sha256:" + _digest(conversation_id, [r[0] for r in factor_rows])
                cur.execute("""
                    INSERT INTO pnf.candidate_semantic_product
                    (product_ref,parser_product_key,compiler_ref,factor_count,
                     exact_reopen_validated,candidate_only,creates_semantic_authority,
                     applicability_promoted,claim_truth_promoted)
                    VALUES (%s,%s,%s,%s,false,true,false,false,false)
                    ON CONFLICT (product_ref) DO NOTHING
                """, (product_ref, "re_gpt:semantic_rejuvenation:v1",
                      "re_gpt.postgres_semantic_registration", len(factor_rows)))
    return {"messages": len(rows), "factors": len(factor_rows),
            "products": int(bool(factor_rows))}
