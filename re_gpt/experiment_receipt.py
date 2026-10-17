"""Redaction-first structural receipts for bounded accessibility experiments.

The canonical conversation graph is projected before hashing or diffing.  Raw
message parts, arbitrary metadata values, attachment pointers, credentials and
local paths are deliberately excluded so receipts can be shared for debugging.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .attachments import LocalAttachment, canonical_attachments_for_node


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pointer_type(pointer: str | None) -> str | None:
    raw = str(pointer or "").strip()
    if not raw:
        return None
    if "://" in raw:
        return raw.split("://", 1)[0].lower() or "present"
    if ":" in raw:
        return raw.split(":", 1)[0].lower() or "present"
    return "present"


def _project_attachment(node_id: str, attachment: Any) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "file_id": str(getattr(attachment, "file_id", "") or ""),
        "filename": str(getattr(attachment, "filename", "") or ""),
        "mime_type": getattr(attachment, "mime_type", None),
        "size": getattr(attachment, "size", None),
        "pointer_present": bool(getattr(attachment, "pointer", None)),
        "pointer_type": _pointer_type(getattr(attachment, "pointer", None)),
    }


def _project_node(chat: Mapping[str, Any], node_id: str, node: Mapping[str, Any], current_node: str | None) -> dict[str, Any]:
    message = _mapping(node.get("message"))
    author = _mapping(message.get("author"))
    content = _mapping(message.get("content"))
    metadata = _mapping(message.get("metadata"))
    attachments = [
        _project_attachment(node_id, item)
        for item in canonical_attachments_for_node(chat, node_id)
    ]
    attachments.sort(key=lambda item: (item["file_id"], item["filename"], item["mime_type"] or ""))
    return {
        "node_id": node_id,
        "parent": str(node.get("parent")) if node.get("parent") is not None else None,
        "children": [str(item) for item in (node.get("children") or [])],
        "role": str(author.get("role")) if author.get("role") is not None else None,
        "content_type": str(content.get("content_type")) if content.get("content_type") is not None else None,
        "status": str(message.get("status")) if message.get("status") is not None else None,
        "end_turn": message.get("end_turn") if isinstance(message.get("end_turn"), bool) else None,
        "create_time": message.get("create_time") if isinstance(message.get("create_time"), (int, float)) else None,
        "update_time": message.get("update_time") if isinstance(message.get("update_time"), (int, float)) else None,
        "metadata_keys": sorted(str(key) for key in metadata.keys()),
        "attachments": attachments,
        "is_current": bool(current_node is not None and node_id == current_node),
    }


def project_graph(chat: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic structural projection of a conversation graph."""

    mapping = _mapping(chat.get("mapping"))
    current_raw = chat.get("current_node")
    current_node = str(current_raw) if current_raw is not None else None
    nodes: dict[str, dict[str, Any]] = {}
    for raw_node_id in sorted(mapping, key=lambda value: str(value)):
        node_id = str(raw_node_id)
        node = _mapping(mapping.get(raw_node_id))
        nodes[node_id] = _project_node(chat, node_id, node, current_node)
    return {
        "conversation_id": str(chat.get("id")) if chat.get("id") is not None else None,
        "current_node": current_node,
        "nodes": nodes,
    }


def graph_snapshot(chat: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize a graph by hashing its redacted structural projection only."""

    projection = project_graph(chat)
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "digest": digest,
        "node_count": len(projection["nodes"]),
        "current_node": projection["current_node"],
    }


def _edges(projected: Mapping[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    nodes = _mapping(projected.get("nodes"))
    for node_id, raw in nodes.items():
        node = _mapping(raw)
        parent = node.get("parent")
        if parent is not None:
            result.add((str(parent), str(node_id)))
    return result


def _attachments(projected: Mapping[str, Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    nodes = _mapping(projected.get("nodes"))
    for node_id, raw in nodes.items():
        node = _mapping(raw)
        for item in node.get("attachments") or []:
            if not isinstance(item, Mapping):
                continue
            safe = {
                "node_id": str(node_id),
                "file_id": str(item.get("file_id") or ""),
                "filename": str(item.get("filename") or ""),
                "mime_type": item.get("mime_type"),
                "size": item.get("size"),
                "pointer_present": bool(item.get("pointer_present")),
                "pointer_type": item.get("pointer_type"),
            }
            key = (
                safe["node_id"], safe["file_id"], safe["filename"], safe["mime_type"],
                safe["size"], safe["pointer_present"], safe["pointer_type"],
            )
            result[key] = safe
    return result


def diff_graphs(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Return a value-redacted structural delta between two conversation graphs."""

    left = project_graph(before)
    right = project_graph(after)
    left_nodes = _mapping(left.get("nodes"))
    right_nodes = _mapping(right.get("nodes"))
    left_ids = set(str(key) for key in left_nodes)
    right_ids = set(str(key) for key in right_nodes)

    changed_nodes: list[dict[str, Any]] = []
    for node_id in sorted(left_ids & right_ids):
        old = _mapping(left_nodes.get(node_id))
        new = _mapping(right_nodes.get(node_id))
        field_names = sorted(
            key for key in set(old) | set(new)
            if key != "node_id" and old.get(key) != new.get(key)
        )
        if field_names:
            changed_nodes.append({"node_id": node_id, "fields": field_names})

    left_edges = _edges(left)
    right_edges = _edges(right)
    left_attachments = _attachments(left)
    right_attachments = _attachments(right)

    return {
        "added_nodes": sorted(right_ids - left_ids),
        "removed_nodes": sorted(left_ids - right_ids),
        "changed_nodes": changed_nodes,
        "added_edges": [list(edge) for edge in sorted(right_edges - left_edges)],
        "removed_edges": [list(edge) for edge in sorted(left_edges - right_edges)],
        "current_node_before": left.get("current_node"),
        "current_node_after": right.get("current_node"),
        "added_attachments": [right_attachments[key] for key in sorted(right_attachments.keys() - left_attachments.keys())],
        "removed_attachments": [left_attachments[key] for key in sorted(left_attachments.keys() - right_attachments.keys())],
    }


def summarize_protocol_stages(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only structural protocol evidence needed for stage comparison."""

    ordered = sorted(
        (item for item in observations if isinstance(item, Mapping)),
        key=lambda item: int(item.get("sequence_index") or 0),
    )
    result: list[dict[str, Any]] = []
    for item in ordered:
        result.append({
            "sequence_index": int(item.get("sequence_index") or 0),
            "stage": str(item.get("bootstrap_stage") or "unclassified"),
            "method": str(item.get("method") or ""),
            "path": str(item.get("path") or item.get("route") or ""),
            "status": item.get("status") if isinstance(item.get("status"), int) else None,
            "response_class": str(item.get("response_class") or ""),
            "body_keys": sorted(str(key) for key in (item.get("body_keys") or [])),
            "has_authorization": bool(item.get("has_authorization") or item.get("auth_header_present")),
            "has_device_id": bool(item.get("has_device_id") or item.get("device_id_present")),
            "has_route_headers": bool(item.get("has_route_headers")),
            "has_route_cookie": bool(item.get("has_route_cookie")),
            "has_sentinel_token": bool(item.get("has_sentinel_token")),
        })
    return result


def _safe_local_attachment(item: LocalAttachment | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item, LocalAttachment):
        return {
            "filename": item.filename,
            "mime_type": item.mime_type,
            "size": item.size,
            "sha256": item.sha256,
        }
    return {
        "filename": str(item.get("filename") or ""),
        "mime_type": item.get("mime_type"),
        "size": item.get("size") if isinstance(item.get("size"), int) else item.get("size_bytes"),
        "sha256": str(item.get("sha256") or ""),
    }


def _safe_canonical_receipt(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    return {
        "state": str(raw.get("state") or "commit_unknown"),
        "user_node_id": str(raw.get("user_node_id")) if raw.get("user_node_id") else None,
        "assistant_node_id": str(raw.get("assistant_node_id")) if raw.get("assistant_node_id") else None,
        "attachment_ids": [str(item) for item in (raw.get("attachment_ids") or []) if item],
    }


def _safe_read_receipt(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    return {
        "adapter": str(raw.get("adapter") or raw.get("read_adapter") or "") or None,
        "attempted_adapters": [str(item) for item in (raw.get("attempted_adapters") or raw.get("attempted_read_adapters") or [])],
        "rejected_adapters": [str(item) for item in (raw.get("rejected_adapters") or raw.get("rejected_read_adapters") or [])],
        "observations": summarize_protocol_stages(raw.get("observations") or raw.get("read_observations") or []),
    }


def build_experiment_receipt(
    *,
    operation: str,
    actuator: str,
    conversation_id: str,
    started_at: float,
    completed_at: float,
    local_attachments: Sequence[LocalAttachment | Mapping[str, Any]] = (),
    protocol_observations: Sequence[Mapping[str, Any]] = (),
    graph_before: Mapping[str, Any],
    graph_after: Mapping[str, Any] | None = None,
    canonical_receipt: Mapping[str, Any] | None = None,
    read_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    actuator_attempts: int = 1,
    capture_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a self-contained, share-safe experiment receipt."""

    reads = read_receipts or {}
    error = capture_error or {}
    safe_error = None
    if error:
        safe_error = {
            "stage": str(error.get("stage") or "unknown"),
            "class": str(error.get("class") or "Error"),
        }
    return {
        "schema_version": 1,
        "operation": str(operation),
        "actuator": str(actuator),
        "conversation_id": str(conversation_id),
        "started_at": float(started_at),
        "completed_at": float(completed_at),
        "actuator_attempts": int(actuator_attempts),
        "local_inputs": {
            "attachments": [_safe_local_attachment(item) for item in local_attachments],
        },
        "protocol_observations": summarize_protocol_stages(protocol_observations),
        "protocol_stages": summarize_protocol_stages(protocol_observations),
        "graph_before": graph_snapshot(graph_before),
        "graph_after": graph_snapshot(graph_after) if graph_after is not None else None,
        "graph_delta": diff_graphs(graph_before, graph_after) if graph_after is not None else None,
        "canonical_receipt": _safe_canonical_receipt(canonical_receipt),
        "read_receipts": {
            "before": _safe_read_receipt(reads.get("before")),
            "after": _safe_read_receipt(reads.get("after")),
        },
        "capture_error": safe_error,
    }


def serialize_experiment_receipt(receipt: Mapping[str, Any]) -> str:
    """Serialize an already-redacted receipt deterministically as UTF-8 JSON text."""

    return json.dumps(dict(receipt), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
