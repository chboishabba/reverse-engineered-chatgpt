from __future__ import annotations

import asyncio
import json

from re_gpt import read_diagnostics
from re_gpt.read_diagnostics import redacted_request_shape


def test_redacted_request_shape_keeps_names_not_values() -> None:
    receipt = redacted_request_shape(
        adapter="sync",
        method="GET",
        route="/backend-api/conversation/<id>",
        headers={
            "Authorization": "Bearer TOP-SECRET",
            "Accept": "application/json",
            "OAI-Device-Id": "device-secret",
        },
        cookie_names=["__Secure-next-auth.session-token", "cf_clearance"],
        cookie_domains=["chatgpt.com", ".chatgpt.com"],
        status=429,
        elapsed_ms=12.5,
        response_class="http_error",
    )
    rendered = json.dumps(receipt)
    assert receipt["header_names"] == ["Accept", "Authorization", "OAI-Device-Id"]
    assert receipt["auth_header_present"] is True
    assert receipt["device_id_present"] is True
    assert "TOP-SECRET" not in rendered
    assert "device-secret" not in rendered
    assert receipt["cookie_names"] == ["__Secure-next-auth.session-token", "cf_clearance"]


def test_sync_probe_returns_receipt_when_client_init_fails(monkeypatch) -> None:
    class BrokenSync:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("HTTP 429 Too Many Requests")

    monkeypatch.setattr(read_diagnostics, "SyncChatGPT", BrokenSync)
    receipt = read_diagnostics._sync_probe("abc", token="token", model=None, user_agent="ua")
    assert receipt["adapter"] == "sync"
    assert receipt["status"] == 429
    assert receipt["response_class"] == "RuntimeError"
    assert receipt["header_names"] == []


def test_async_probe_returns_receipt_when_client_enter_fails(monkeypatch) -> None:
    class BrokenAsync:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("HTTP 403 rejected")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(read_diagnostics, "AsyncChatGPT", BrokenAsync)
    receipt = asyncio.run(
        read_diagnostics._async_probe("abc", token="token", model=None, user_agent="ua")
    )
    assert receipt["adapter"] == "async"
    assert receipt["status"] == 403
    assert receipt["response_class"] == "RuntimeError"
    assert receipt["header_names"] == []
