"""Derived retrieval/follow artifact helpers focused on conversation list operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

def build_conversation_list_follow_artifact(
    *,
    query: str,
    result_refs: Sequence[str] | None,
    total_results: int,
    max_results: int = 10,
    stop_after: bool = True,
    trigger_command: str = "re_gpt --list",
    trigger_params: Mapping[str, object] | None = None,
    profile_version: str = "re_gpt.list_follow.v1",
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """Produce a derived follow artifact describing the conversation catalog retrieval."""

    selected = list(result_refs or [])[:max_results]
    truncated = len(result_refs or []) > max_results if result_refs is not None else False
    artifact_id = artifact_id or f"re_gpt.list_follow:{uuid4()}"

    return {
        "schema_version": "re_gpt.list_follow.raw.v1",
        "artifact_type": "derived.re_gpt.list_follow",
        "artifact_id": artifact_id,
        "retrieval_envelope": {
            "trigger_command": trigger_command,
            "trigger_params": dict(trigger_params or {}),
            "scope": {
                "query": query,
                "total_results": int(total_results),
                "max_results": int(max_results),
                "stop_after": stop_after,
                "truncated": truncated,
            },
            "result_refs": selected,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "retrieved": len(selected),
            "total_results": int(total_results),
            "truncated": truncated,
        },
        "non_authoritative": True,
        "bounded": True,
    }


def write_conversation_list_follow_artifact(out_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_conversation_list_follow_artifact(**kwargs)
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def build_conversation_list_follow_normalized_artifact(
    *,
    query: str,
    result_refs: Sequence[str] | None,
    total_results: int,
    max_results: int = 10,
    stop_after: bool = True,
    trigger_command: str = "re_gpt --list",
    trigger_params: Mapping[str, object] | None = None,
    profile_version: str = "re_gpt.list_follow.v1",
    artifact_id: str | None = None,
) -> dict[str, Any]:
    raw = build_conversation_list_follow_artifact(
        query=query,
        result_refs=result_refs,
        total_results=total_results,
        max_results=max_results,
        stop_after=stop_after,
        trigger_command=trigger_command,
        trigger_params=trigger_params,
        profile_version=profile_version,
        artifact_id=artifact_id,
    )
    envelope = raw["retrieval_envelope"]
    result_refs_limited = list(envelope.get("result_refs") or [])
    raw_artifact_id = str(raw["artifact_id"])
    query_text = str(query).strip()

    return {
        "schema_version": "itir.normalized.artifact.v1",
        "artifact_role": "derived_product",
        "artifact_id": f"re_gpt.list_follow.normalized:{raw_artifact_id}",
        "canonical_identity": {
            "identity_class": "chatgpt_conversation_list_follow",
            "identity_key": raw_artifact_id,
            "aliases": [f"query:{query_text}"] if query_text else [],
        },
        "provenance_anchor": {
            "source_system": "reverse-engineered-chatgpt",
            "source_artifact_id": raw_artifact_id,
            "anchor_kind": "conversation_list",
            "anchor_ref": query_text or None,
        },
        "context_envelope_ref": {
            "envelope_id": f"re_gpt.list_follow_context:{raw_artifact_id}",
            "envelope_kind": "chatgpt_conversation_list_context",
        },
        "authority": {
            "authority_class": "derived_inspection",
            "derived": True,
            "promotion_receipt_ref": None,
        },
        "lineage": {
            "upstream_artifact_ids": result_refs_limited,
            "profile_version": profile_version,
        },
        "follow_obligation": {
            "trigger": query_text or "re_gpt --list",
            "scope": f"review up to {int(max_results)} conversation list results from reverse-engineered-chatgpt",
            "stop_condition": (
                "stop after reviewing the bounded conversation list result set"
                if stop_after
                else "continue only under an explicit new bounded follow decision"
            ),
        },
        "unresolved_pressure_status": "follow_needed",
        "summary": {
            "producer": "reverse-engineered-chatgpt",
            "query": query_text or None,
            "retrieved": len(result_refs_limited),
            "total_results": int(total_results),
            "truncated": bool(envelope.get("scope", {}).get("truncated", False)),
        },
    }


def write_conversation_list_follow_normalized_artifact(out_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_conversation_list_follow_normalized_artifact(**kwargs)
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
