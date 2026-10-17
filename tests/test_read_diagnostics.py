from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

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


def test_diagnose_reads_still_runs_browser_without_direct_token(monkeypatch) -> None:
    monkeypatch.setattr(read_diagnostics, "get_session_token", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    monkeypatch.setattr(read_diagnostics, "get_default_model", lambda: None)
    monkeypatch.setattr(read_diagnostics, "get_default_user_agent", lambda: "ua")
    monkeypatch.setattr(
        read_diagnostics,
        "_sync_probe",
        lambda conversation_id, **kwargs: {"adapter": "sync", "token": kwargs["token"]},
    )

    async def fake_async(conversation_id, **kwargs):
        return {"adapter": "async", "token": kwargs["token"]}

    monkeypatch.setattr(read_diagnostics, "_async_probe", fake_async)
    monkeypatch.setattr(
        read_diagnostics,
        "fetch_conversation_same_origin",
        lambda *args, **kwargs: SimpleNamespace(
            diagnostic={
                "adapter": "browser",
                "method": "GET",
                "route": "/backend-api/conversation/<id>",
                "header_names": ["Accept", "Authorization"],
                "cookie_names": [],
                "cookie_domains": [],
                "auth_header_present": True,
                "device_id_present": False,
                "status": 200,
                "response_class": "ok",
            }
        ),
    )
    receipts = read_diagnostics.diagnose_reads("abc")
    assert [row["adapter"] for row in receipts] == ["async", "sync", "browser"]
    assert receipts[0]["token"] is None
    assert receipts[1]["token"] is None
    assert receipts[2]["status"] == 200
