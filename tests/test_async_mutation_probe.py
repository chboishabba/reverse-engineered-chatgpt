from __future__ import annotations

import json
from types import SimpleNamespace
import pytest

from re_gpt.async_mutation_probe import (
    AmbiguousAsyncWriteError,
    ExplicitAsyncWriteRejection,
    submit_async_prompt_once,
)


class FakeSession:
    def __init__(self, *, post_status: int = 200, post_exc: Exception | None = None, chunks: list[str] | None = None) -> None:
        self.post_status = post_status
        self.post_exc = post_exc
        self.chunks = chunks if chunks is not None else [
            'data: {"message": {"id": "a1", "author": {"role": "assistant"}, "content": {"parts": ["ACK"]}}, "conversation_id": "conv-1"}\n\n',
            "data: [DONE]\n\n",
        ]
        self.post_calls = 0
        self.cookies = [
            SimpleNamespace(name="__Secure-next-auth.session-token", value="SECRET_SESSION", domain="chatgpt.com"),
            SimpleNamespace(name="oai-chat-web-route", value="SECRET_ROUTE", domain=".chatgpt.com"),
        ]

    async def post(self, *, url: str, headers: dict, json: dict, content_callback=None):
        self.post_calls += 1
        if self.post_exc is not None:
            raise self.post_exc
        if content_callback is not None:
            for chunk in self.chunks:
                content_callback(chunk.encode("utf-8"))
        return SimpleNamespace(status_code=self.post_status, text="SECRET_ERROR_BODY")


class FakeConversation:
    def __init__(self, *, build_exc: Exception | None = None) -> None:
        self.build_exc = build_exc
        self.build_calls = 0

    async def build_message_payload(self, prompt: str) -> dict:
        self.build_calls += 1
        if self.build_exc is not None:
            raise self.build_exc
        return {
            "action": "next",
            "conversation_id": "conv-1",
            "parent_message_id": "parent-1",
            "model": "gpt-4",
            "messages": [
                {
                    "id": "u-msg-1",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                }
            ],
        }


class FakeAsyncClient:
    def __init__(
        self,
        *,
        session: FakeSession,
        conv: FakeConversation,
        sentinel_exc: Exception | None = None,
        exit_exc: Exception | None = None,
    ) -> None:
        self.session = session
        self._conv = conv
        self.sentinel_exc = sentinel_exc
        self.exit_exc = exit_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.exit_exc is not None:
            raise self.exit_exc
        return False

    def get_conversation(self, _conversation_id: str) -> FakeConversation:
        return self._conv

    def build_request_headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer SECRET_BEARER",
            "Oai-device-id": "SECRET_DEVICE",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
        }

    async def create_chat_requirements_token(self) -> str:
        if self.sentinel_exc is not None:
            raise self.sentinel_exc
        return "SECRET_SENTINEL"


def test_async_probe_accepted_sse_emits_redacted_observations_and_calls_verify() -> None:
    session = FakeSession(post_status=200)
    conv = FakeConversation()
    verified: list[tuple[str, str]] = []

    result = submit_async_prompt_once(
        "conv-1",
        "SECRET_PROMPT_TEXT",
        session_token="SECRET_TOKEN",
        client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
        verify_callback=lambda cid, p: verified.append((cid, p)) or {"verified": True},
    )

    assert session.post_calls == 1
    assert result["state"] == "submission_observed"
    assert result["transport"] == "async"
    assert result["assistant_preview_observed"] is True
    assert result["verification"] == {"verified": True}
    assert verified == [("conv-1", "SECRET_PROMPT_TEXT")]
    assert len(result["protocol_observations"]) == 3

    serialized = json.dumps(result, sort_keys=True)
    for secret in (
        "SECRET_PROMPT_TEXT",
        "SECRET_TOKEN",
        "SECRET_BEARER",
        "SECRET_DEVICE",
        "SECRET_SENTINEL",
        "SECRET_SESSION",
        "SECRET_ROUTE",
    ):
        assert secret not in serialized


def test_async_probe_explicit_http_rejection_never_retries() -> None:
    session = FakeSession(post_status=403, chunks=[])
    conv = FakeConversation()

    with pytest.raises(ExplicitAsyncWriteRejection) as excinfo:
        submit_async_prompt_once(
            "conv-1",
            "hello",
            session_token="SECRET_TOKEN",
            client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
        )

    assert session.post_calls == 1
    assert excinfo.value.stage == "conversation_post"
    assert excinfo.value.status == 403
    serialized = json.dumps([obs.to_dict() for obs in excinfo.value.observations])
    assert "SECRET_ERROR_BODY" not in serialized


def test_async_probe_5xx_is_ambiguous_and_never_retries() -> None:
    session = FakeSession(post_status=503, chunks=[])
    conv = FakeConversation()

    with pytest.raises(AmbiguousAsyncWriteError) as excinfo:
        submit_async_prompt_once(
            "conv-1",
            "hello",
            session_token="SECRET_TOKEN",
            client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
        )

    assert session.post_calls == 1
    assert excinfo.value.status == 503


def test_async_probe_timeout_or_reset_raises_ambiguous_without_retry() -> None:
    session = FakeSession(post_exc=TimeoutError("stream timed out after send"))
    conv = FakeConversation()

    with pytest.raises(AmbiguousAsyncWriteError) as excinfo:
        submit_async_prompt_once(
            "conv-1",
            "hello",
            session_token="SECRET_TOKEN",
            client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
        )

    assert session.post_calls == 1
    assert len(excinfo.value.observations) == 3
    assert excinfo.value.observations[-1].bootstrap_stage == "conversation_post"
    assert excinfo.value.observations[-1].response_class == "TimeoutError"


def test_async_probe_exit_failure_after_post_is_ambiguous() -> None:
    session = FakeSession(post_status=200)
    conv = FakeConversation()

    with pytest.raises(AmbiguousAsyncWriteError):
        submit_async_prompt_once(
            "conv-1",
            "hello",
            session_token="SECRET_TOKEN",
            client_factory=lambda **kwargs: FakeAsyncClient(
                session=session,
                conv=conv,
                exit_exc=RuntimeError("close failed"),
            ),
        )

    assert session.post_calls == 1
