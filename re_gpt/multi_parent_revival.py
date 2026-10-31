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
    db_path: Path | str = DEFAULT_DB_PATH,
    session_token: str | None = None,
    model: str | None = None,
    follow: bool = True,
    follow_timeout: float = 180.0,
) -> dict[str, Any]:
    """Create one fresh child and attach one explicit handoff artifact exactly once."""
    parents = [str(item).strip() for item in parent_thread_ids]
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
    try:
        handoff = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("handoff artifact must be UTF-8 JSON") from exc
    if not isinstance(handoff, dict) or handoff.get("version") != "itir.pnf.multi_parent_handoff.v1":
        raise ValueError("unsupported multi-parent handoff artifact version")
    artifact_parents = handoff.get("parents")
    if not isinstance(artifact_parents, list) or [
        item.get("canonical_thread_id") for item in artifact_parents if isinstance(item, dict)
    ] != parents:
        raise ValueError("handoff artifact parent order does not match explicit parent_thread_ids")
    candidate_world = handoff.get("candidate_world")
    formal_gap = handoff.get("formal_gap")
    if not isinstance(candidate_world, dict) or candidate_world.get("status") != "candidate_only":
        raise ValueError("handoff artifact is not explicitly candidate-only")
    if not isinstance(formal_gap, dict) or formal_gap.get("selected_context_sufficiency_proved") is not False:
        raise ValueError("handoff artifact must preserve the open selected-context sufficiency gap")
    digest = hashlib.sha256(payload).hexdigest()
    service = MutationService(db_path=db_path, session_token=session_token, model=model)
    parent_list = ", ".join(f"`{item}`" for item in parents)
    created = service.new_conversation(
        f"Governed multi-parent revival. Canonical parents: {parent_list}. "
        f"Candidate handoff SHA-256: {digest}. The attached artifact is candidate-only.",
        follow=False,
    )
    child = str(created.get("conversation_id") or created.get("resolved_thread_id") or "").strip()
    if not child or created.get("state") not in {"complete", "committed", "committed_pending"}:
        # In particular, commit_unknown is never retried or followed by attachment.
        return {
            "state": created.get("state", "commit_unknown"),
            "child_conversation_id": child or None,
            "creation_operation": created,
            "attachment_dispatched": False,
            "automatic_resend_allowed": False,
        }
    receipt = record_multi_parent_lineage(
        db_path, child, parents, handoff_path=artifact, handoff_sha256=digest,
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
    with sqlite3.connect(Path(db_path).expanduser()) as db:
        db.execute(
            "UPDATE conversation_lineage_multi_parent SET attachment_operation_id=?,"
            "attachment_state=?,updated_at=? WHERE child_conversation_id=?",
            (operation_id, state, time.time(), child),
        )
    return {
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
        "automatic_resend_allowed": False,
        "transport": "direct_async_cffi",
        "browser_dom_allowed": False,
    }
