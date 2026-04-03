"""Producer-owned normalized artifact helpers for re_gpt."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_conversation_source_artifact(
    *,
    conversation_id: str,
    title: str | None,
    json_path: str | None,
    remote_update_time: float | None,
    total_messages: int,
    new_messages: int,
    asset_count: int = 0,
    profile_version: str = "re_gpt.conversation_source.v1",
) -> dict[str, Any]:
    safe_title = (title or "").strip()
    aliases = [f"chatgpt.conversation:{conversation_id}"]
    if safe_title:
        aliases.append(f"title:{safe_title}")

    return {
        "schema_version": "itir.normalized.artifact.v1",
        "artifact_role": "source_artifact",
        "artifact_id": f"re_gpt.conversation:{conversation_id}",
        "canonical_identity": {
            "identity_class": "chatgpt_conversation",
            "identity_key": conversation_id,
            "aliases": aliases,
        },
        "provenance_anchor": {
            "source_system": "reverse-engineered-chatgpt",
            "source_artifact_id": conversation_id,
            "anchor_kind": "conversation_export" if json_path else "live_conversation_pull",
            "anchor_ref": json_path or conversation_id,
        },
        "context_envelope_ref": {
            "envelope_id": f"re_gpt.conversation_context:{conversation_id}",
            "envelope_kind": "chatgpt_conversation_context",
        },
        "authority": {
            "authority_class": "archive",
            "derived": False,
            "promotion_receipt_ref": None,
        },
        "lineage": {
            "upstream_artifact_ids": [value for value in [json_path, conversation_id] if value],
            "profile_version": profile_version,
        },
        "follow_obligation": None,
        "unresolved_pressure_status": "none",
        "summary": {
            "producer": "reverse-engineered-chatgpt",
            "conversation_id": conversation_id,
            "title": safe_title or None,
            "remote_update_time": remote_update_time,
            "total_messages": total_messages,
            "new_messages": new_messages,
            "asset_count": asset_count,
            "json_path": json_path,
        },
    }


def write_conversation_source_artifact(
    out_path: str | Path,
    *,
    conversation_id: str,
    title: str | None,
    json_path: str | None,
    remote_update_time: float | None,
    total_messages: int,
    new_messages: int,
    asset_count: int = 0,
    profile_version: str = "re_gpt.conversation_source.v1",
) -> dict[str, Any]:
    payload = build_conversation_source_artifact(
        conversation_id=conversation_id,
        title=title,
        json_path=json_path,
        remote_update_time=remote_update_time,
        total_messages=total_messages,
        new_messages=new_messages,
        asset_count=asset_count,
        profile_version=profile_version,
    )
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
