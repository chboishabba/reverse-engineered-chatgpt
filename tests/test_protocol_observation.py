from __future__ import annotations

import json

from re_gpt.protocol_observation import (
    ProtocolObservation,
    build_protocol_observation,
    normalize_protocol_path,
    observation_from_exception,
    sanitize_cookies,
    sanitize_headers,
    sanitize_json_schema,
    sanitize_sse_event_shapes,
    status_from_exception,
)


def test_normalize_protocol_path_redacts_uuids_and_conversation_ids() -> None:
    assert normalize_protocol_path("https://chatgpt.com/backend-api/conversation/6acc564d-0314-83ec-8036-1f4d9868c5f3?foo=bar") == "/backend-api/conversation/<id>"
    assert normalize_protocol_path("/backend-api/conversations/custom-slug") == "/backend-api/conversations/<id>"


def test_protocol_observation_strips_secret_values_and_transcript_text() -> None:
    secret_bearer = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.SECRET"
    secret_device = "device-uuid-secret-999"
    secret_sentinel = "sentinel-proof-token-xyz"
    secret_cookie = "cookie-session-value-abc"
    secret_prompt = "Super sensitive prompt transcript text"
    obs = build_protocol_observation(
        transport="browser", operation="continue", method="POST",
        path="https://chatgpt.com/backend-api/conversation", status=200,
        response_class="sse_stream",
        headers={
            "Authorization": secret_bearer,
            "OAI-Device-Id": secret_device,
            "OpenAI-Sentinel-Chat-Requirements-Token": secret_sentinel,
            "X-OpenAI-Target-Route": "route-secret",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
        },
        cookies=[
            {"name": "__Secure-next-auth.session-token", "value": secret_cookie, "domain": "chatgpt.com"},
            {"name": "oai-chat-web-route", "value": "affinity-secret", "domain": ".chatgpt.com"},
            {"name": "oai-did", "value": secret_device, "domain": "chatgpt.com"},
        ],
        payload={
            "action": "next",
            "conversation_id": "6ad2c994-f4d4-83ec-9658-c9f1f84e5db0",
            "parent_message_id": "parent-node-secret",
            "messages": [{"id": "msg-1", "author": {"role": "user"}, "content": {"content_type": "text", "parts": [secret_prompt]}}],
        },
        sse_chunks=[
            'data: {"message": {"id": "a1", "author": {"role": "assistant"}, "content": {"parts": ["SECRET REPLY"]}}, "conversation_id": "c1"}\n\n',
            "data: [DONE]\n\n",
        ],
        sequence_index=3, elapsed_ms=42.125,
    )
    assert isinstance(obs, ProtocolObservation)
    serialized = json.dumps(obs.to_dict(), sort_keys=True)
    for forbidden in (secret_bearer, secret_device, secret_sentinel, secret_cookie, secret_prompt, "SECRET REPLY", "affinity-secret", "route-secret", "parent-node-secret"):
        assert forbidden not in serialized
    assert obs.has_authorization is True
    assert obs.has_device_id is True
    assert obs.has_route_headers is True
    assert obs.has_route_cookie is True
    assert obs.has_sentinel_token is True
    assert obs.body_keys == ["action", "conversation_id", "messages", "parent_message_id"]
    assert obs.sse_shape["done_seen"] is True
    assert obs.sse_shape["assistant_message_events"] == 1


def test_observation_from_exception_redacts_error_body() -> None:
    exc = RuntimeError('HTTP 429 Too Many Requests: {"detail": "SECRET_ERROR_TOKEN"}')
    obs = observation_from_exception(
        exc, transport="async", operation="exact_read", method="GET",
        path="/backend-api/conversation/6acc564d-0314-83ec-8036-1f4d9868c5f3",
        headers={"Authorization": "Bearer TOP-SECRET"}, cookies={"cf_clearance": "secret-cf"}, elapsed_ms=11.2,
    )
    rendered = json.dumps(obs.to_dict())
    assert obs.status == 429
    assert obs.response_class == "RuntimeError"
    assert obs.path == "/backend-api/conversation/<id>"
    assert "SECRET_ERROR_TOKEN" not in rendered
    assert "TOP-SECRET" not in rendered
    assert "secret-cf" not in rendered


def test_status_parser_ignores_unlabeled_three_digit_numbers() -> None:
    assert status_from_exception(RuntimeError("operation took 429 milliseconds")) is None
    assert status_from_exception(RuntimeError("HTTP 429 Too Many Requests")) == 429
    assert status_from_exception(RuntimeError("status=503 upstream")) == 503


def test_sanitizers_handle_empty_and_non_json_inputs() -> None:
    assert sanitize_headers(None)["header_names"] == []
    assert sanitize_cookies(None)["cookie_names"] == []
    keys, schema = sanitize_json_schema("not-json-at-all")
    assert keys == []
    assert schema == {"type": "non_json_text"}
    sse = sanitize_sse_event_shapes(["not-sse-line\n"])
    assert sse["data_event_count"] == 0
    assert sse["done_seen"] is False
