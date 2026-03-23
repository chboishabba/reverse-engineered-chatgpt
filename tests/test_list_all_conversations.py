import os
import sys
import asyncio
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.async_chatgpt import AsyncChatGPT


class _CookieRecorder:
    def __init__(self):
        self.values = {}

    def set(self, name, value, domain=None, path=None):
        self.values[(name, domain or "", path or "/")] = value


class _FakeResponse:
    def __init__(self, payload, *, cookies=None):
        self._payload = payload
        self.text = json.dumps(payload)
        self.status_code = 200
        self.headers = {}
        self.cookies = cookies or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_sync_list_all_conversations_pagination(monkeypatch):
    client = SyncChatGPT()

    pages = [
        {
            "items": [
                {"id": "1", "title": "a", "update_time": 1},
                {"id": "2", "title": "b", "update_time": 2},
            ]
        },
        {"items": [{"id": "3", "title": "c", "update_time": 3}]},
    ]
    calls = []

    def fake_page(offset=0, limit=28):
        calls.append((offset, limit))
        return pages.pop(0)

    monkeypatch.setattr(client, "list_conversations_page", fake_page)

    result = client.list_all_conversations(limit=2)

    assert result == [
        {"id": "1", "title": "a", "last_updated": 1},
        {"id": "2", "title": "b", "last_updated": 2},
        {"id": "3", "title": "c", "last_updated": 3},
    ]
    assert calls == [(0, 2), (2, 2)]


def test_async_list_all_conversations_pagination(monkeypatch):
    client = AsyncChatGPT()

    pages = [
        {
            "items": [
                {"id": "1", "title": "a", "update_time": 1},
                {"id": "2", "title": "b", "update_time": 2},
            ]
        },
        {"items": [{"id": "3", "title": "c", "update_time": 3}]},
    ]
    calls = []

    async def fake_page(offset=0, limit=28):
        calls.append((offset, limit))
        return pages.pop(0)

    monkeypatch.setattr(client, "list_conversations_page", fake_page)

    result = asyncio.run(client.list_all_conversations(limit=2))

    assert result == [
        {"id": "1", "title": "a", "last_updated": 1},
        {"id": "2", "title": "b", "last_updated": 2},
        {"id": "3", "title": "c", "last_updated": 3},
    ]
    assert calls == [(0, 2), (2, 2)]


def test_async_fetch_conversation_removes_stream_headers(monkeypatch):
    client = AsyncChatGPT()
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    class FakeSession:
        async def get(self, url=None, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["params"] = dict(params or {})
            return FakeResponse()

    monkeypatch.setattr(client, "build_request_headers", lambda: {"Content-Type": "application/json"})
    client.session = FakeSession()

    result = asyncio.run(client.fetch_conversation("conv-1", since_time=12.5))

    assert result == {"ok": True}
    assert captured["url"].endswith("/conversation/conv-1")
    assert "Content-Type" not in captured["headers"]
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["params"]["since_time"] == "12.5"


def test_sync_fetch_auth_token_bootstraps_frontend_cookies_on_warning_banner():
    client = SyncChatGPT(session_token="session-token")
    client._frontend_cookies = {"__Secure-next-auth.session-token": "session-token"}

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.cookies = _CookieRecorder()

        def get(self, url=None, headers=None, cookies=None):
            self.calls.append({"url": url, "cookies": dict(cookies or {})})
            if url == "https://chatgpt.com/":
                return _FakeResponse({}, cookies={"oai-did": "device-cookie", "cf_clearance": "cf-cookie"})
            if len([call for call in self.calls if call["url"].endswith("/api/auth/session")]) == 1:
                return _FakeResponse({"WARNING_BANNER": "warning"})
            return _FakeResponse({"accessToken": "access-token"})

    client.session = FakeSession()

    token = client.fetch_auth_token()

    assert token == "access-token"
    auth_calls = [call for call in client.session.calls if call["url"].endswith("/api/auth/session")]
    assert len(auth_calls) == 2
    assert auth_calls[0]["cookies"] == {"__Secure-next-auth.session-token": "session-token"}
    assert auth_calls[1]["cookies"]["__Secure-next-auth.session-token"] == "session-token"
    assert "oai-did" in auth_calls[1]["cookies"]


def test_async_fetch_auth_token_bootstraps_frontend_cookies_on_warning_banner():
    client = AsyncChatGPT(session_token="session-token")
    client._frontend_cookies = {"__Secure-next-auth.session-token": "session-token"}

    class FakeAsyncSession:
        def __init__(self):
            self.calls = []
            self.cookies = _CookieRecorder()

        async def get(self, url=None, headers=None, cookies=None):
            self.calls.append({"url": url, "cookies": dict(cookies or {})})
            if url == "https://chatgpt.com/":
                return _FakeResponse({}, cookies={"oai-did": "device-cookie", "cf_clearance": "cf-cookie"})
            if len([call for call in self.calls if call["url"].endswith("/api/auth/session")]) == 1:
                return _FakeResponse({"WARNING_BANNER": "warning"})
            return _FakeResponse({"accessToken": "access-token"})

    client.session = FakeAsyncSession()

    token = asyncio.run(client.fetch_auth_token())

    assert token == "access-token"
    auth_calls = [call for call in client.session.calls if call["url"].endswith("/api/auth/session")]
    assert len(auth_calls) == 2
    assert auth_calls[0]["cookies"] == {"__Secure-next-auth.session-token": "session-token"}
    assert auth_calls[1]["cookies"]["__Secure-next-auth.session-token"] == "session-token"
    assert "oai-did" in auth_calls[1]["cookies"]


def test_sync_fetch_auth_token_uses_client_bootstrap_access_token():
    client = SyncChatGPT(session_token="session-token")
    client._frontend_cookies = {"__Secure-next-auth.session-token": "session-token"}
    bootstrap_html = (
        '<html><script type="application/json" id="client-bootstrap">'
        '{"session":{"accessToken":"bootstrap-token"}}'
        '</script></html>'
    )

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.cookies = _CookieRecorder()

        def get(self, url=None, headers=None, cookies=None):
            self.calls.append({"url": url, "cookies": dict(cookies or {})})
            if url == "https://chatgpt.com/":
                return _FakeResponse({}, cookies={"oai-did": "device-cookie", "cf_clearance": "cf-cookie"})
            return _FakeResponse({"WARNING_BANNER": "warning"})

    client.session = FakeSession()
    client._bootstrap_frontend_cookies = lambda: bootstrap_html

    token = client.fetch_auth_token()

    assert token == "bootstrap-token"


def test_async_fetch_auth_token_uses_client_bootstrap_access_token():
    client = AsyncChatGPT(session_token="session-token")
    client._frontend_cookies = {"__Secure-next-auth.session-token": "session-token"}
    bootstrap_html = (
        '<html><script type="application/json" id="client-bootstrap">'
        '{"session":{"accessToken":"bootstrap-token"}}'
        '</script></html>'
    )

    class FakeAsyncSession:
        def __init__(self):
            self.calls = []
            self.cookies = _CookieRecorder()

        async def get(self, url=None, headers=None, cookies=None):
            self.calls.append({"url": url, "cookies": dict(cookies or {})})
            if url == "https://chatgpt.com/":
                return _FakeResponse({}, cookies={"oai-did": "device-cookie", "cf_clearance": "cf-cookie"})
            return _FakeResponse({"WARNING_BANNER": "warning"})

    client.session = FakeAsyncSession()

    async def fake_bootstrap():
        return bootstrap_html

    client._bootstrap_frontend_cookies = fake_bootstrap

    token = asyncio.run(client.fetch_auth_token())

    assert token == "bootstrap-token"


def test_async_fetch_auth_token_uses_sync_bootstrap_fallback_when_async_home_is_logged_out():
    client = AsyncChatGPT(session_token="session-token")
    client._frontend_cookies = {"__Secure-next-auth.session-token": "session-token"}
    async_bootstrap_html = (
        '<html><script type="application/json" id="client-bootstrap">'
        '{"authStatus":"logged_out","session":null}'
        '</script></html>'
    )
    sync_bootstrap_html = (
        '<html><script type="application/json" id="client-bootstrap">'
        '{"session":{"accessToken":"bootstrap-token"}}'
        '</script></html>'
    )

    class FakeAsyncSession:
        def __init__(self):
            self.calls = []
            self.cookies = _CookieRecorder()

        async def get(self, url=None, headers=None, cookies=None):
            self.calls.append({"url": url, "cookies": dict(cookies or {})})
            return _FakeResponse({"WARNING_BANNER": "warning"})

    client.session = FakeAsyncSession()

    async def fake_bootstrap():
        return async_bootstrap_html

    client._bootstrap_frontend_cookies = fake_bootstrap
    client._bootstrap_frontend_cookies_sync_fallback = lambda: (
        sync_bootstrap_html,
        {"oai-did": "device-cookie"},
    )

    token = asyncio.run(client.fetch_auth_token())

    assert token == "bootstrap-token"
