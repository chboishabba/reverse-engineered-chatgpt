"""Multi-parent revival adapter over an explicit candidate handoff artifact.

Canonical source selection belongs to ITIR. This module accepts only an explicit
handoff file and canonical parent IDs; its SQLite rows are transport/lineage
receipts, never corpus content or source-selection authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Sequence

from .mutation_service import MutationService
from .storage import DEFAULT_DB_PATH

_HANDOFF_KEYS = {
    "version", "child_thread_id", "parent_count", "parents", "candidate_world",
    "materialization", "lineage_receipt", "governance", "authority",
    "formal_gap", "packet_id",
}
_SOURCE_REF_KEYS = {
    "message_id", "source_message_id", "node_id", "parent_node_id",
    "branch_membership", "source_thread_id", "lineage_status", "ts", "role",
    "body_sha256", "archive_locator",
}
_SPAN_KEYS = {"kind", "start", "end", "authority"}


def _validate_handoff(handoff: Any, parents: list[str]) -> None:
    """Validate the complete non-promoting handoff boundary before any write."""
    if not isinstance(handoff, dict) or set(handoff) != _HANDOFF_KEYS:
        raise ValueError("handoff artifact has an unsupported or incomplete field set")
    if handoff.get("version") != "itir.pnf.multi_parent_handoff.v1":
        raise ValueError("unsupported multi-parent handoff artifact version")
    if handoff.get("parent_count") != len(parents):
        raise ValueError("handoff parent_count does not match explicit parent IDs")
    artifact_parents = handoff.get("parents")
    if (
        not isinstance(artifact_parents, list)
        or len(artifact_parents) != len(parents)
        or any(not isinstance(item, dict) for item in artifact_parents)
        or [item.get("canonical_thread_id") for item in artifact_parents] != parents
    ):
        raise ValueError("handoff artifact parent order does not match explicit parent_thread_ids")

    for parent in artifact_parents:
        if set(parent) != {
            "canonical_thread_id", "packet_id", "source_refs", "span_receipt",
            "semantic_pnf_execution",
        }:
            raise ValueError("parent projection has an unsupported field set")
        refs = parent.get("source_refs")
        if not isinstance(refs, list):
            raise ValueError("parent source_refs must be an array")
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != _SOURCE_REF_KEYS:
                raise ValueError("source references must contain coordinates and digests only")
            if not isinstance(ref.get("body_sha256"), str) or len(ref["body_sha256"]) != 64:
                raise ValueError("source reference requires a body SHA-256")
        spans_receipt = parent.get("span_receipt")
        if not isinstance(spans_receipt, dict) or spans_receipt.get("full_thread_scanned") is not True:
            raise ValueError("parent projection requires a full-thread scan receipt")
        semantic = parent.get("semantic_pnf_execution")
        if (
            not isinstance(semantic, dict)
            or semantic.get("candidate_only") is not True
            or semantic.get("no_promotion") is not True
            or semantic.get("status") != "typed_pnf_not_compiled"
        ):
            raise ValueError("parent semantic projection must remain an uncompiled candidate")

    candidate = handoff.get("candidate_world")
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {
            "status", "nodes", "cross_parent_equivalences",
            "cross_parent_contradictions", "reviewed_world_model", "formal_proof",
        }
        or candidate.get("status") != "candidate_only"
        or candidate.get("reviewed_world_model") is not False
        or candidate.get("formal_proof") is not False
        or candidate.get("cross_parent_equivalences") != []
        or candidate.get("cross_parent_contradictions") != []
        or not isinstance(candidate.get("nodes"), list)
        or len(candidate["nodes"]) > 80
    ):
        raise ValueError("handoff candidate world violates candidate-only non-promotion invariants")
    for node in candidate["nodes"]:
        if (
            not isinstance(node, dict)
            or set(node) != {"parent_thread_id", "source_ref", "candidate_spans", "authority"}
            or node.get("parent_thread_id") not in parents
            or node.get("authority") != "raw_source_reference_or_lexical_candidate"
            or not isinstance(node.get("source_ref"), dict)
            or set(node["source_ref"]) != _SOURCE_REF_KEYS
            or not isinstance(node.get("candidate_spans"), list)
        ):
            raise ValueError("candidate node is outside the bounded source-reference schema")
        for span in node["candidate_spans"]:
            if (
                not isinstance(span, dict)
                or set(span) != _SPAN_KEYS
                or span.get("authority") != "sensiblaw_public_token_span_candidate"
                or not isinstance(span.get("start"), int)
                or not isinstance(span.get("end"), int)
                or span["start"] < 0
                or span["end"] < span["start"]
            ):
                raise ValueError("candidate spans must be public-parser offset candidates")

    materialization = handoff.get("materialization")
    if (
        not isinstance(materialization, dict)
        or materialization != {
            "artifact_content": "canonical_source_references_and_lexical_candidate_spans_only",
            "raw_transcript_included": False,
            "typed_world_model_included": False,
            "reviewed_claims_included": False,
            "proof_included": False,
            "source_content_materialization": "not_authorized_by_read_only_projection",
        }
    ):
        raise ValueError("handoff materialization exceeds the read-only candidate projection")

    lineage = handoff.get("lineage_receipt")
    if (
        not isinstance(lineage, dict)
        or lineage.get("relationship") != "multi_parent_proposed"
        or lineage.get("parents") != [{"canonical_thread_id": item} for item in parents]
        or lineage.get("parents_remain_independent_authorities") is not True
        or lineage.get("parent_retirement") is not False
        or lineage.get("durability") != "handoff_receipt_only_until_child_attachment_receipt"
    ):
        raise ValueError("handoff lineage must preserve ordered, independent parent authorities")

    authority = handoff.get("authority")
    if (
        not isinstance(authority, dict)
        or authority.get("canonical_archive") != "corpus_authority_read_only"
        or authority.get("transport_receipts") != "bounded_operation_evidence_only"
        or authority.get("candidate_world") != "not_reviewed_not_promoted"
        or authority.get("equivalence") != "not_established"
        or authority.get("formal_proof") != "not_established"
        or authority.get("dispatch") is not False
        or authority.get("automatic_resend") is not False
    ):
        raise ValueError("handoff authority boundary is missing or unsafe")
    gap = handoff.get("formal_gap")
    if (
        not isinstance(gap, dict)
        or gap.get("status") != "open"
        or gap.get("selected_context_sufficiency_proved") is not False
    ):
        raise ValueError("handoff must preserve the open selected-context sufficiency gap")
    governance = handoff.get("governance")
    if (
        not isinstance(governance, dict)
        or governance.get("dispatch_allowed") is not False
        or governance.get("automatic_resend_allowed") is not False
        or governance.get("browser_dom_allowed") is not False
    ):
        raise ValueError("handoff governance must remain read-only and no-resend")


def record_multi_parent_lineage(
    db_path: Path | str,
    child_conversation_id: str,
    parent_thread_ids: Sequence[str],
    *,
    handoff_path: Path | str,
    handoff_sha256: str,
    attachment_operation_id: str | None = None,
    attachment_state: str = "pending",
) -> dict[str, Any]:
    """Persist a durable ordered lineage receipt in the operational receipt store."""
    parents = [str(item).strip() for item in parent_thread_ids]
    if not 2 <= len(parents) <= 10 or any(not item for item in parents):
        raise ValueError("parent_thread_ids must contain 2..10 non-empty canonical IDs")
    if len(set(parents)) != len(parents):
        raise ValueError("duplicate parent IDs are not allowed")
    child = str(child_conversation_id).strip()
    if not child:
        raise ValueError("child conversation ID is required")
    if len(handoff_sha256) != 64:
        raise ValueError("handoff SHA-256 is required")
    path = str(Path(handoff_path).expanduser().resolve())
    now = time.time()
    with sqlite3.connect(Path(db_path).expanduser()) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_lineage_multi_parent (
                child_conversation_id TEXT PRIMARY KEY,
                handoff_path TEXT NOT NULL,
                handoff_sha256 TEXT NOT NULL,
                attachment_operation_id TEXT,
                attachment_state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_lineage_multi_parent_edges (
                child_conversation_id TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                canonical_parent_thread_id TEXT NOT NULL,
                PRIMARY KEY (child_conversation_id, parent_ordinal),
                UNIQUE (child_conversation_id, canonical_parent_thread_id),
                FOREIGN KEY (child_conversation_id)
                    REFERENCES conversation_lineage_multi_parent(child_conversation_id)
            )
        """)
        db.execute(
            "INSERT INTO conversation_lineage_multi_parent "
            "(child_conversation_id,handoff_path,handoff_sha256,attachment_operation_id,"
            "attachment_state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (child, path, handoff_sha256, attachment_operation_id, attachment_state, now, now),
        )
        db.executemany(
            "INSERT INTO conversation_lineage_multi_parent_edges "
            "(child_conversation_id,parent_ordinal,canonical_parent_thread_id) VALUES (?,?,?)",
            [(child, index, parent) for index, parent in enumerate(parents)],
        )
    return {
        "child_conversation_id": child,
        "canonical_parent_thread_ids": parents,
        "handoff_sha256": handoff_sha256,
        "attachment_operation_id": attachment_operation_id,
        "attachment_state": attachment_state,
        "receipt_store_authority": "operational_lineage_receipt_only",
    }


def revive_from_handoff(
    parent_thread_ids: Sequence[str],
    handoff_path: Path | str,
    prompt: str,
    *,
    request_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
    session_token: str | None = None,
    model: str | None = None,
    follow: bool = True,
    follow_timeout: float = 180.0,
) -> dict[str, Any]:
    """Create one fresh child and attach one explicit handoff artifact exactly once."""
    if any(not isinstance(item, str) for item in parent_thread_ids):
        raise ValueError("parent_thread_ids must contain canonical string IDs")
    parents = [item.strip() for item in parent_thread_ids]
    if not 2 <= len(parents) <= 10 or any(not item for item in parents):
        raise ValueError("parent_thread_ids must contain 2..10 canonical IDs")
    if len(set(parents)) != len(parents):
        raise ValueError("duplicate parent IDs are not allowed")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    artifact = Path(handoff_path).expanduser()
    if not artifact.is_file():
        raise FileNotFoundError("explicit handoff_path does not exist")
    payload = artifact.read_bytes()
    if not payload:
        raise ValueError("handoff artifact is empty")
    if len(payload) > 100_000:
        raise ValueError("handoff artifact exceeds the 100000-byte transport ceiling")
    try:
        handoff = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("handoff artifact must be UTF-8 JSON") from exc
    _validate_handoff(handoff, parents)
    digest = hashlib.sha256(payload).hexdigest()
    request_key = str(request_id).strip()
    if not request_key or len(request_key) > 128:
        raise ValueError("request_id must be a non-empty stable caller-supplied key (max 128 chars)")
    fingerprint = hashlib.sha256(json.dumps({
        "parents": parents, "handoff_sha256": digest,
        "prompt_sha256": hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    db_file = Path(db_path).expanduser()
    with sqlite3.connect(db_file) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS multi_parent_revival_requests (
                request_id TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                child_conversation_id TEXT,
                creation_operation_id TEXT,
                attachment_operation_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        existing = db.execute(
            "SELECT request_fingerprint,state,child_conversation_id,"
            "creation_operation_id,attachment_operation_id "
            "FROM multi_parent_revival_requests WHERE request_id=?",
            (request_key,),
        ).fetchone()
        if existing:
            if existing[0] != fingerprint:
                raise ValueError("request_id was already used for a different revival payload")
            return {
                "request_id": request_key, "state": existing[1],
                "child_conversation_id": existing[2],
                "creation_operation_id": existing[3],
                "attachment_operation_id": existing[4],
                "replayed_request": True, "attachment_dispatched": False,
                "automatic_resend_allowed": False,
            }
        now = time.time()
        db.execute(
            "INSERT INTO multi_parent_revival_requests "
            "(request_id,request_fingerprint,state,created_at,updated_at) "
            "VALUES (?,?, 'creation_starting', ?, ?)",
            (request_key, fingerprint, now, now),
        )
    service = MutationService(db_path=db_path, session_token=session_token, model=model)
    parent_list = ", ".join(f"\`{item}\`" for item in parents)
    created = service.new_conversation(
        f"Governed multi-parent revival. Canonical parents: {parent_list}. "
        f"Candidate handoff SHA-256: {digest}. The attached artifact is candidate-only.",
        follow=False,
    )
    child = str(created.get("conversation_id") or created.get("resolved_thread_id") or "").strip()
    if not child or created.get("state") not in {"complete", "committed", "committed_pending"}:
        state = str(created.get("state") or "commit_unknown")
        with sqlite3.connect(db_file) as db:
            db.execute(
                "UPDATE multi_parent_revival_requests SET state=?,child_conversation_id=?,"
                "creation_operation_id=?,updated_at=? WHERE request_id=?",
                (state, child or None, created.get("operation_id"), time.time(), request_key),
            )
        return {
            "request_id": request_key, "state": state,
            "child_conversation_id": child or None,
            "creation_operation": created,
            "attachment_dispatched": False,
            "automatic_resend_allowed": False,
        }
    receipt = record_multi_parent_lineage(
        db_path, child, parents, handoff_path=artifact, handoff_sha256=digest,
    )
    with sqlite3.connect(db_file) as db:
        db.execute(
            "UPDATE multi_parent_revival_requests SET state='attachment_starting',"
            "child_conversation_id=?,creation_operation_id=?,updated_at=? WHERE request_id=?",
            (child, created.get("operation_id"), time.time(), request_key),
        )
    attached = service.continue_thread(
        child,
        f"Attach and inspect the candidate-only revival handoff. "
        f"Preserve all parent authorities independently. Do not infer equivalence, "
        f"reviewed truth, or proof from shared candidates. Continue this task: {prompt.strip()}",
        attachments=[artifact],
        write_transport="async",
        dom_fallback=False,
        follow=follow,
        follow_timeout=follow_timeout,
    )
    operation_id = str(attached.get("operation_id") or "").strip() or None
    state = str(attached.get("state") or "unknown")
    with sqlite3.connect(db_file) as db:
        db.execute(
            "UPDATE conversation_lineage_multi_parent SET attachment_operation_id=?,"
            "attachment_state=?,updated_at=? WHERE child_conversation_id=?",
            (operation_id, state, time.time(), child),
        )
        db.execute(
            "UPDATE multi_parent_revival_requests SET state=?,attachment_operation_id=?,"
            "updated_at=? WHERE request_id=?",
            (state, operation_id, time.time(), request_key),
        )
    return {
        "request_id": request_key,
        "state": state,
        "child_conversation_id": child,
        "canonical_parent_thread_ids": parents,
        "handoff_sha256": digest,
        "lineage_receipt": receipt,
        "attachment_receipt": {
            "operation_id": operation_id,
            "state": state,
            "attachment_ids": attached.get("attachment_ids", []),
            "canonical_attachment_count": attached.get("canonical_attachment_count"),
            "user_node_id": attached.get("user_node_id"),
            "verified_by_mutation_service": bool(attached.get("canonical_attachment_count")),
        },
        "attachment_dispatched": True,
        "replayed_request": False,
        "automatic_resend_allowed": False,
        "transport": "direct_async_cffi",
        "browser_dom_allowed": False,
    }
