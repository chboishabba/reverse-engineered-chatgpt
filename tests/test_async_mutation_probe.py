from __future__ import annotations

import json
from types import SimpleNamespace
import pytest

from re_gpt.browser_session_replay import BrowserSessionSnapshot

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
        self.calls: list[dict] = []
        self.cookies = [
            SimpleNamespace(name="__Secure-next-auth.session-token", value="SECRET_SESSION", domain="chatgpt.com"),
            SimpleNamespace(name="oai-chat-web-route", value="SECRET_ROUTE", domain=".chatgpt.com"),
        ]

    async def post(self, *, url: str, headers: dict, json: dict, content_callback=None):
        self.post_calls += 1
        self.calls.append({"url": url, "headers": dict(headers), "json": dict(json)})
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


def test_browser_session_uses_observed_frontend_prepare_then_single_submit() -> None:
    session = FakeSession(post_status=200)
    conv = FakeConversation()
    snapshot = BrowserSessionSnapshot(
        access_token="SECRET_TOKEN",
        user_agent="Mozilla/5.0",
        fingerprint={"timezone": "Australia/Brisbane", "timezone_offset_min": -600},
    )

    result = submit_async_prompt_once(
        "conv-1",
        "SECRET_PROMPT_TEXT",
        browser_session=snapshot,
        allow_browser_bootstrap=False,
        client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
    )

    assert result["state"] == "submission_observed"
    assert [call["url"].rsplit("/", 1)[-1] for call in session.calls] == ["prepare", "conversation"]
    prepare, submit = session.calls
    assert prepare["url"].endswith("/backend-api/f/conversation/prepare")
    assert prepare["json"]["client_prepare_state"] == "sent"
    assert prepare["json"]["partial_query"]["id"] == submit["json"]["messages"][0]["id"]
    assert submit["url"].endswith("/backend-api/f/conversation")
    assert submit["json"]["client_prepare_state"] == "success"
    assert "openai-sentinel-chat-requirements-token" not in {
        key.lower() for key in submit["headers"]
    }
    stages = [obs["bootstrap_stage"] for obs in result["protocol_observations"]]
    assert stages == ["conversation_get", "conversation_prepare", "conversation_post"]


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


class FakeAttachmentSession(FakeSession):
    def __init__(
        self,
        *,
        create_status: int = 200,
        put_status: int = 201,
        process_status: int = 200,
        process_chunks: list[str] | None = None,
    ) -> None:
        super().__init__(post_status=200)
        self.create_status = create_status
        self.put_status = put_status
        self.process_status = process_status
        self.process_chunks = process_chunks if process_chunks is not None else [
            '{"file_id":"file_test_123","event":"file.processing.started","progress":0.0,"extra":null}\n',
            '{"file_id":"file_test_123","event":"file.processing.completed","progress":100.0,"extra":{"library_persistence_result":"temporary","library_persistence_reason":"library_storage_limit_exceeded"}}\n',
        ]
        self.put_calls: list[dict] = []
        self.posted_payloads: list[tuple[str, dict]] = []

    async def post(self, *, url: str, headers: dict, json: dict, content_callback=None):
        self.posted_payloads.append((url, json))
        if url.endswith("/backend-api/files"):
            return SimpleNamespace(
                status_code=self.create_status,
                json=lambda: {
                    "status": "success",
                    "file_id": "file_test_123",
                    "upload_url": "https://sdmntpr.oaiusercontent.com/files/raw?sv=SECRET_SAS_SIG",
                },
                text="SECRET_FILES_ERROR",
            )
        if url.endswith("/backend-api/files/process_upload_stream"):
            if content_callback is not None:
                for chunk in self.process_chunks:
                    content_callback(chunk.encode("utf-8"))
            return SimpleNamespace(status_code=self.process_status, text="")
        return await super().post(
            url=url,
            headers=headers,
            json=json,
            content_callback=content_callback,
        )

    async def put(self, *, url: str, headers: dict, data: bytes):
        self.put_calls.append({"url": url, "headers": headers, "size": len(data)})
        return SimpleNamespace(status_code=self.put_status, text="SECRET_PUT_ERROR")


def test_async_probe_stages_attachment_and_attaches_metadata_without_leaking_sas_url(tmp_path) -> None:
    att_file = tmp_path / "probe.txt"
    att_file.write_text("SECRET_FILE_BODY_DO_NOT_LEAK\n", encoding="utf-8")
    session = FakeAttachmentSession()
    conv = FakeConversation()

    result = submit_async_prompt_once(
        "conv-1",
        "SECRET_PROMPT_TEXT",
        attachments=[att_file],
        session_token="SECRET_TOKEN",
        client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
    )

    assert result["state"] == "submission_observed"
    assert result["attachment_ids"] == ["file_test_123"]
    assert "attachment_ready" in result["stage_times"]
    assert len(session.put_calls) == 1
    assert session.put_calls[0]["headers"]["x-ms-blob-type"] == "BlockBlob"

    conv_url, conv_payload = session.posted_payloads[-1]
    assert conv_url.endswith("/conversation")
    attachments_meta = conv_payload["messages"][0]["metadata"]["attachments"]
    assert attachments_meta == [
        {
            "id": "file_test_123",
            "name": "probe.txt",
            "size": att_file.stat().st_size,
            "mime_type": "text/plain",
            "library_persistence_result": "temporary",
            "library_persistence_reason": "library_storage_limit_exceeded",
        }
    ]

    stages = [obs["bootstrap_stage"] for obs in result["protocol_observations"]]
    assert stages == [
        "conversation_get",
        "attachment_staging",
        "attachment_upload",
        "attachment_process",
        "sentinel_requirements",
        "conversation_post",
    ]

    serialized = json.dumps(result, sort_keys=True)
    for secret in (
        "SECRET_SAS_SIG",
        "sdmntpr.oaiusercontent.com",
        "SECRET_FILE_BODY_DO_NOT_LEAK",
        "SECRET_PROMPT_TEXT",
        "SECRET_TOKEN",
    ):
        assert secret not in serialized


def test_async_probe_attachment_upload_failure_is_precommit_rejection(tmp_path) -> None:
    att_file = tmp_path / "probe.txt"
    att_file.write_text("hello\n", encoding="utf-8")
    session = FakeAttachmentSession(put_status=403)
    conv = FakeConversation()

    with pytest.raises(ExplicitAsyncWriteRejection) as excinfo:
        submit_async_prompt_once(
            "conv-1",
            "hello",
            attachments=[att_file],
            session_token="SECRET_TOKEN",
            client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
        )

    assert excinfo.value.stage == "attachment_upload"
    assert excinfo.value.status == 403
    assert session.post_calls == 0



class CallbackThenTimeoutSession(FakeSession):
    async def post(self, *, url: str, headers: dict, json: dict, content_callback=None):
        self.post_calls += 1
        if content_callback is not None:
            for chunk in self.chunks:
                content_callback(chunk.encode("utf-8"))
        raise TimeoutError("SECRET_TIMEOUT_PAYLOAD")


@pytest.mark.parametrize(
    ("chunks", "terminal"),
    [
        (["data: [DO", "NE]\\n\\n"], True),
        (["data: {bad json}\\n\\n"], False),
        (["data: [DO"], False),
        ([], False),
    ],
)
def test_post_timeout_incremental_terminal_evidence_is_redacted(chunks, terminal) -> None:
    session = CallbackThenTimeoutSession(chunks=[c.replace("\\n", "\n") for c in chunks])
    conv = FakeConversation()
    with pytest.raises(AmbiguousAsyncWriteError) as caught:
        submit_async_prompt_once(
            "conv-1", "SECRET_PROMPT_BODY",
            client_factory=lambda **kwargs: FakeAsyncClient(session=session, conv=conv),
            allow_browser_bootstrap=False,
        )
    error = caught.value
    assert session.post_calls == 1
    assert error.transport_timeout is True
    assert error.stream_evidence["stream_terminal_observed"] is terminal
    assert error.expected_user_message_id == "u-msg-1"
    serialized = json.dumps(error.stream_evidence) + str(error)
    for secret in ("SECRET_PROMPT_BODY", "SECRET_SESSION", "SECRET_TIMEOUT_PAYLOAD", "Bearer"):
        assert secret not in serialized
