from __future__ import annotations

from re_gpt import mcp_server


def test_session_status_mcp_is_redacted(monkeypatch) -> None:
    class FakeLifecycle:
        def status(self, **kwargs):
            return {"state": "ready", "access_token": {"present": True, "state": "fresh"}}
    monkeypatch.setattr(mcp_server, "SessionLifecycle", lambda: FakeLifecycle())
    result = mcp_server.chatgpt_session_status()
    assert result["state"] == "ready"
    assert "token" not in str(result).lower().replace("access_token", "")


def test_protocol_compare_mcp_returns_drift_classification() -> None:
    base = {"schema_version": 1, "routes": [], "header_names": ["authorization"], "body_keys": [], "body_schema_signatures": [], "attachment_stages": [], "sse_event_key_signatures": []}
    candidate = {**base, "header_names": ["authorization", "x-new"]}
    result = mcp_server.chatgpt_protocol_compare(base, candidate)
    assert result["classification"] == "drift_suspected"
