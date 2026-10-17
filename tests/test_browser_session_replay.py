from __future__ import annotations

import json
import stat
import time
from pathlib import Path

from re_gpt.async_chatgpt import AsyncChatGPT
from re_gpt.async_mutation_probe import submit_async_prompt_once
from re_gpt.browser_protocol_recorder import BrowserProtocolRecorder
from re_gpt.browser_session_replay import (
    BrowserSessionSnapshot,
    apply_browser_session_to_client,
    load_browser_session_snapshot,
    mark_sentinel_consumed,
    save_browser_session_snapshot,
)
from re_gpt.sync_chatgpt import SyncChatGPT


def _sample_snapshot() -> BrowserSessionSnapshot:
    now = time.time()
    return BrowserSessionSnapshot(
        captured_at=now,
        profile_dir="/tmp/fake-profile",
        access_token="ACCESS_TOKEN_123",
        account_id="acct-uuid-1",
        device_id="did-uuid-1",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/154.0.0.0",
        cookies=[
            {"name": "__Secure-next-auth.session-token", "value": "sess-val-1", "domain": ".chatgpt.com", "path": "/"},
            {"name": "cf_clearance", "value": "cf-val-1", "domain": ".chatgpt.com", "path": "/"},
            {"name": "oai-did", "value": "did-uuid-1", "domain": ".chatgpt.com", "path": "/"},
            {"name": "oai-sc", "value": "sc-val-1", "domain": ".chatgpt.com", "path": "/"},
            {"name": "_account", "value": "acct-uuid-1", "domain": ".chatgpt.com", "path": "/"},
        ],
        headers={
            "chatgpt-account-id": "acct-uuid-1",
            "oai-did": "did-uuid-1",
            "oai-language": "en-US",
            "originator": "Codex Browser",
            "sec-ch-ua": '"Chromium";v="154"',
            "sec-ch-ua-platform": '"Linux"',
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/154.0.0.0",
            "x-openai-codex-window-type": "browser",
            "x-openai-web-frontend": "codex_webview",
        },
        fingerprint={
            "timezone": "Australia/Brisbane",
            "timezone_offset_min": -600,
            "client_contextual_info": {
                "app_name": "chatgpt.com",
                "app_surface": "codex_browser",
                "has_web_push_capabilities": True,
                "web_push_notification_permission": "default",
            },
        },
        sentinel={
            "chat_requirements_token": "SENTINEL_REQ_TOK",
            "proof_token": "SENTINEL_POW_TOK",
            "turnstile_token": "SENTINEL_TURNSTILE_TOK",
            "prepare_token": "SENTINEL_PREP_TOK",
            "expire_after": 600,
            "expire_at": now + 600,
            "captured_at": now,
            "consumed": False,
        },
    )


def test_snapshot_save_load_and_consume_roundtrip(tmp_path: Path) -> None:
    snap = _sample_snapshot()
    saved_path = save_browser_session_snapshot(snap, profile_dir=tmp_path)
    assert saved_path.is_file()
    assert stat.S_IMODE(saved_path.stat().st_mode) == 0o600

    loaded = load_browser_session_snapshot(profile_dir=tmp_path)
    assert loaded is not None
    assert loaded.access_token == "ACCESS_TOKEN_123"
    assert loaded.account_id == "acct-uuid-1"
    assert loaded.device_id == "did-uuid-1"
    assert loaded.has_fresh_sentinel() is True
    assert loaded.sentinel_headers() == {
        "openai-sentinel-chat-requirements-token": "SENTINEL_REQ_TOK",
        "openai-sentinel-proof-token": "SENTINEL_POW_TOK",
        "openai-sentinel-turnstile-token": "SENTINEL_TURNSTILE_TOK",
    }

    summary = loaded.to_redacted_summary()
    serialized_summary = json.dumps(summary)
    assert "ACCESS_TOKEN_123" not in serialized_summary
    assert "SENTINEL_REQ_TOK" not in serialized_summary
    assert summary["has_fresh_sentinel"] is True

    mark_sentinel_consumed(profile_dir=tmp_path)
    after_consume = load_browser_session_snapshot(profile_dir=tmp_path)
    assert after_consume is not None
    assert after_consume.has_fresh_sentinel() is False


def test_apply_browser_session_to_async_and_sync_clients() -> None:
    snap = _sample_snapshot()
    async_client = AsyncChatGPT(browser_session=snap)
    apply_browser_session_to_client(async_client, snap)
    headers = async_client.build_request_headers()

    assert headers["Authorization"] == "Bearer ACCESS_TOKEN_123"
    assert headers["chatgpt-account-id"] == "acct-uuid-1"
    assert headers["oai-did"] == "did-uuid-1"
    assert headers["Oai-device-id"] == "did-uuid-1"
    assert headers["x-openai-web-frontend"] == "codex_webview"
    assert headers["originator"] == "Codex Browser"
    assert headers["User-Agent"] == "Mozilla/5.0 (X11; Linux x86_64) Chrome/154.0.0.0"
    assert async_client._frontend_cookies["cf_clearance"] == "cf-val-1"

    sync_client = SyncChatGPT(browser_session=snap)
    apply_browser_session_to_client(sync_client, snap)
    sync_headers = sync_client.build_request_headers()
    assert sync_headers["chatgpt-account-id"] == "acct-uuid-1"
    assert sync_headers["oai-did"] == "did-uuid-1"


def test_async_probe_replays_browser_session_and_sentinel_headers(tmp_path: Path) -> None:
    snap = _sample_snapshot()
    save_browser_session_snapshot(snap, profile_dir=tmp_path)

    captured_post: dict[str, object] = {}

    class FakeSession:
        def __init__(self) -> None:
            self.cookies = {}

        async def post(self, *, url: str, headers: dict, json: dict, content_callback=None):
            captured_post["url"] = url
            captured_post["headers"] = dict(headers)
            captured_post["json"] = dict(json)
            if content_callback is not None:
                content_callback(
                    b'data: {"message": {"id": "a1", "author": {"role": "assistant"}, "content": {"parts": ["ACK"]}}, "conversation_id": "conv-1"}\n\n'
                )
                content_callback(b"data: [DONE]\n\n")

            class Resp:
                status_code = 200
                text = ""

            return Resp()

    class FakeConv:
        async def build_message_payload(self, user_input: str) -> dict:
            return {
                "action": "next",
                "conversation_id": "conv-1",
                "parent_message_id": "parent-1",
                "model": "gpt-6-thinking",
                "messages": [
                    {
                        "id": "msg-1",
                        "author": {"role": "user"},
                        "create_time": 1700000000.0,
                        "content": {"content_type": "text", "parts": [user_input]},
                    }
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.session = FakeSession()
            self._frontend_cookies = {}
            self._replayed_browser_snapshot = None
            self.auth_token = None
            self.devive_id = "default-did"
            self.user_agent = "default-ua"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_conversation(self, _cid: str) -> FakeConv:
            return FakeConv()

        def build_request_headers(self) -> dict[str, str]:
            snap_ref = self._replayed_browser_snapshot
            base = {
                "Authorization": f"Bearer {self.auth_token}",
                "User-Agent": self.user_agent,
                "Oai-device-id": self.devive_id,
                "Content-Type": "application/json",
            }
            if snap_ref is not None:
                base.update(snap_ref.build_replay_headers())
            return base

    result = submit_async_prompt_once(
        "conv-1",
        "Reply with only ACK.",
        profile_dir=tmp_path,
        allow_browser_bootstrap=False,
        client_factory=FakeClient,
    )

    assert result["state"] == "submission_observed"
    assert str(captured_post["url"]).endswith("/backend-api/f/conversation")
    sent_headers = captured_post["headers"]
    assert isinstance(sent_headers, dict)
    assert sent_headers["openai-sentinel-chat-requirements-token"] == "SENTINEL_REQ_TOK"
    assert sent_headers["openai-sentinel-proof-token"] == "SENTINEL_POW_TOK"
    assert sent_headers["openai-sentinel-turnstile-token"] == "SENTINEL_TURNSTILE_TOK"
    assert sent_headers["chatgpt-account-id"] == "acct-uuid-1"
    assert sent_headers["oai-did"] == "did-uuid-1"

    after = load_browser_session_snapshot(profile_dir=tmp_path)
    assert after is not None
    assert after.has_fresh_sentinel() is False


def test_recorder_flushes_in_flight_requests_on_stop() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.listeners: dict[str, list] = {}

        def on(self, event: str, handler) -> None:
            self.listeners.setdefault(event, []).append(handler)

        def remove_listener(self, event: str, handler) -> None:
            if event in self.listeners and handler in self.listeners[event]:
                self.listeners[event].remove(handler)

    class FakeReq:
        url = "https://chatgpt.com/backend-api/f/conversation"
        method = "POST"
        headers = {"authorization": "Bearer TOK", "oai-did": "did-1", "openai-sentinel-chat-requirements-token": "S"}
        post_data = '{"action":"next","messages":[{"id":"m1"}]}'

    page = FakePage()
    rec = BrowserProtocolRecorder(page, operation="continue")
    rec.start()
    for h in page.listeners["request"]:
        h(FakeReq())
    obs = rec.stop()
    assert len(obs) == 1
    assert obs[0].path == "/backend-api/f/conversation"
    assert obs[0].response_class == "in_flight"
    assert obs[0].bootstrap_stage == "conversation_post"
