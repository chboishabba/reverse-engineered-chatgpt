from __future__ import annotations

from re_gpt.protocol_drift import build_protocol_fingerprint, compare_protocol_fingerprints


def _obs(*, path="/backend-api/f/conversation", method="POST", stage="conversation_post", headers=None, body_keys=None, event_keys=None):
    return {
        "transport": "browser",
        "operation": "continue",
        "method": method,
        "path": path,
        "bootstrap_stage": stage,
        "header_names": headers or ["authorization", "content-type", "oai-did"],
        "body_keys": body_keys or ["action", "messages", "model", "parent_message_id"],
        "body_schema": {"type": "object", "keys": body_keys or ["action", "messages", "model", "parent_message_id"]},
        "sse_shape": {"event_key_signatures": event_keys or ["conversation_id,message"], "done_seen": True},
    }


def test_fingerprint_is_value_free_and_stable() -> None:
    fp = build_protocol_fingerprint([_obs()])
    assert fp["schema_version"] == 1
    assert fp["routes"] == [{"method": "POST", "path": "/backend-api/f/conversation", "stage": "conversation_post"}]
    assert "authorization" in fp["header_names"]
    assert "conversation_id,message" in fp["sse_event_key_signatures"]
    assert fp["body_schema_signatures"]


def test_identical_fingerprint_is_compatible() -> None:
    base = build_protocol_fingerprint([_obs()])
    result = compare_protocol_fingerprints(base, base)
    assert result["classification"] == "compatible"
    assert result["delta"]["missing_routes"] == []
    assert result["delta"]["added_routes"] == []


def test_additive_structural_change_is_drift_suspected() -> None:
    base = build_protocol_fingerprint([_obs()])
    candidate = build_protocol_fingerprint([_obs(headers=["authorization", "content-type", "oai-did", "x-new-header"])])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "drift_suspected"
    assert "x-new-header" in result["delta"]["added_header_names"]


def test_missing_authorization_header_is_incompatible() -> None:
    base = build_protocol_fingerprint([_obs()])
    candidate = build_protocol_fingerprint([_obs(headers=["content-type", "oai-did"])])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "incompatible"
    assert "authorization" in result["delta"]["missing_header_names"]


def test_missing_critical_route_is_incompatible() -> None:
    base = build_protocol_fingerprint([_obs(), _obs(path="/backend-api/files", stage="attachment_staging")])
    candidate = build_protocol_fingerprint([_obs()])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "incompatible"
    assert result["delta"]["missing_routes"]


def test_removed_body_key_is_incompatible() -> None:
    base = build_protocol_fingerprint([_obs()])
    candidate = build_protocol_fingerprint([_obs(body_keys=["action", "model", "parent_message_id"])])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "incompatible"
    assert "messages" in result["delta"]["missing_body_keys"]


def test_added_sse_event_signature_is_drift_suspected() -> None:
    base = build_protocol_fingerprint([_obs()])
    candidate = build_protocol_fingerprint([_obs(event_keys=["conversation_id,message", "conversation_id,type"])])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "drift_suspected"
    assert "conversation_id,type" in result["delta"]["added_sse_event_key_signatures"]


def test_removed_sse_event_signature_is_incompatible() -> None:
    base = build_protocol_fingerprint([_obs(event_keys=["conversation_id,message", "conversation_id,type"])])
    candidate = build_protocol_fingerprint([_obs(event_keys=["conversation_id,message"])])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "incompatible"
    assert "conversation_id,type" in result["delta"]["missing_sse_event_key_signatures"]


def test_attachment_stage_loss_is_incompatible() -> None:
    base = build_protocol_fingerprint([
        _obs(path="/backend-api/files", stage="attachment_staging"),
        _obs(path="/files/upload", method="PUT", stage="attachment_upload"),
        _obs(path="/backend-api/files/process_upload_stream", stage="attachment_process"),
        _obs(),
    ])
    candidate = build_protocol_fingerprint([_obs()])
    result = compare_protocol_fingerprints(base, candidate)
    assert result["classification"] == "incompatible"
    assert "attachment_upload" in result["delta"]["missing_attachment_stages"]
