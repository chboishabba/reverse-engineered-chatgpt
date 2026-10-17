from __future__ import annotations

import asyncio
import json

from re_gpt.browser_protocol_recorder import (
    BrowserProtocolRecorder,
    is_chatgpt_protocol_url,
)


class FakeRequest:
    def __init__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        post_data: str | None = None,
    ) -> None:
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data


class FakeResponse:
    def __init__(
        self,
        request: FakeRequest,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.request = request
        self.url = request.url
        self.status = status
        self.headers = headers or {}


class FakePage:
    def __init__(self, cookies: list[dict[str, str]] | None = None) -> None:
        self.listeners: dict[str, list] = {}
        self._cookies = cookies or []
        self.context = self

    async def cookies(self, _url: str):
        return list(self._cookies)

    def on(self, event: str, handler) -> None:
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        handlers = self.listeners.get(event) or []
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, payload) -> None:
        for handler in list(self.listeners.get(event) or ()):
            handler(payload)


def test_is_chatgpt_protocol_url_filters_non_chatgpt_traffic() -> None:
    assert is_chatgpt_protocol_url("https://chatgpt.com/backend-api/conversation") is True
    assert is_chatgpt_protocol_url("https://chatgpt.com/api/auth/session") is True
    assert is_chatgpt_protocol_url("https://chatgpt.com/c/6ad2c994-f4d4-83ec-9658-c9f1f84e5db0") is False
    assert is_chatgpt_protocol_url("https://example.com/backend-api/conversation") is False


def test_recorder_captures_ordered_redacted_observations_and_detaches() -> None:
    page = FakePage(
        cookies=[
            {"name": "__Secure-next-auth.session-token", "value": "SECRET_SESSION_COOKIE", "domain": "chatgpt.com"},
            {"name": "oai-chat-web-route", "value": "SECRET_ROUTE_COOKIE", "domain": ".chatgpt.com"},
            {"name": "oai-did", "value": "SECRET_DEVICE_COOKIE", "domain": "chatgpt.com"},
        ]
    )

    async def run():
        async with BrowserProtocolRecorder(page, operation="continue") as recorder:
            # Ignored external request
            ext_req = FakeRequest("https://cdn.oaistatic.com/assets/app.js")
            page.emit("request", ext_req)
            page.emit("response", FakeResponse(ext_req, status=200))

            # 1. Auth session GET
            auth_req = FakeRequest(
                "https://chatgpt.com/api/auth/session",
                method="GET",
                headers={"Accept": "application/json"},
            )
            page.emit("request", auth_req)
            page.emit(
                "response",
                FakeResponse(auth_req, status=200, headers={"content-type": "application/json"}),
            )

            # 2. Sentinel POST
            sentinel_req = FakeRequest(
                "https://chatgpt.com/backend-api/sentinel/chat-requirements",
                method="POST",
                headers={
                    "Authorization": "Bearer SECRET_BEARER_TOKEN",
                    "OAI-Device-Id": "SECRET_DEVICE_HEADER",
                    "Content-Type": "application/json",
                },
                post_data=json.dumps({"p": "SECRET_PROOF"}),
            )
            page.emit("request", sentinel_req)
            page.emit(
                "response",
                FakeResponse(sentinel_req, status=200, headers={"content-type": "application/json"}),
            )

            # 3. Conversation SSE POST
            conv_req = FakeRequest(
                "https://chatgpt.com/backend-api/conversation",
                method="POST",
                headers={
                    "Authorization": "Bearer SECRET_BEARER_TOKEN",
                    "OAI-Device-Id": "SECRET_DEVICE_HEADER",
                    "OpenAI-Sentinel-Chat-Requirements-Token": "SECRET_SENTINEL_TOKEN",
                    "X-OpenAI-Target-Route": "SECRET_TARGET_ROUTE",
                    "Content-Type": "application/json",
                },
                post_data=json.dumps(
                    {
                        "action": "next",
                        "conversation_id": "6ad2c994-f4d4-83ec-9658-c9f1f84e5db0",
                        "parent_message_id": "SECRET_PARENT_NODE",
                        "messages": [
                            {
                                "id": "SECRET_MSG_ID",
                                "author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["SECRET_USER_PROMPT"]},
                            }
                        ],
                    }
                ),
            )
            page.emit("request", conv_req)
            page.emit(
                "response",
                FakeResponse(conv_req, status=200, headers={"content-type": "text/event-stream"}),
            )

        # After stop, events are ignored
        late_req = FakeRequest("https://chatgpt.com/backend-api/conversation/6ad2c994-f4d4-83ec-9658-c9f1f84e5db0")
        page.emit("request", late_req)
        page.emit("response", FakeResponse(late_req, status=200))
        return recorder.to_dicts()

    records = asyncio.run(run())
    assert len(records) == 3
    assert [r["sequence_index"] for r in records] == [1, 2, 3]
    assert [r["bootstrap_stage"] for r in records] == [
        "auth_session",
        "sentinel_requirements",
        "conversation_post",
    ]
    assert records[2]["response_class"] == "sse_stream"
    assert records[2]["has_authorization"] is True
    assert records[2]["has_device_id"] is True
    assert records[2]["has_sentinel_token"] is True
    assert records[2]["has_route_headers"] is True
    assert records[2]["has_route_cookie"] is True
    assert records[2]["body_keys"] == ["action", "conversation_id", "messages", "parent_message_id"]

    serialized = json.dumps(records, sort_keys=True)
    for secret in (
        "SECRET_SESSION_COOKIE",
        "SECRET_ROUTE_COOKIE",
        "SECRET_DEVICE_COOKIE",
        "SECRET_BEARER_TOKEN",
        "SECRET_DEVICE_HEADER",
        "SECRET_PROOF",
        "SECRET_SENTINEL_TOKEN",
        "SECRET_TARGET_ROUTE",
        "SECRET_PARENT_NODE",
        "SECRET_MSG_ID",
        "SECRET_USER_PROMPT",
    ):
        assert secret not in serialized
