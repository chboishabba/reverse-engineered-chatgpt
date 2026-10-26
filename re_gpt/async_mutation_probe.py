"""Opt-in one-attempt async-CFFI mutation probe with browser session/fingerprint replay.

Uses recorded/replayed browser session artifacts (cookies, bearer token,
account/device headers, Client-Hints fingerprint, and browser-minted sentinel
tokens) or existing AsyncConversation payload/sentinel machinery to perform a
single HTTP continue attempt and emits redacted ProtocolObservation records.
Never auto-retries or silently falls back to DOM when an HTTP POST outcome is
ambiguous. Canonical success requires external graph verification.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import inspect
import json
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

from .async_bridge import run_async_sync
from .async_chatgpt import AsyncChatGPT, CHATGPT_API
from .attachments import LocalAttachment
from .browser_session_replay import (
    BrowserSessionSnapshot,
    apply_browser_session_to_client,
    capture_browser_session_snapshot_async,
    load_browser_session_snapshot,
    mark_sentinel_consumed,
    refresh_browser_session_sentinel_async,
)
from .browser_observer import fetch_conversation_same_origin
from .protocol_observation import (
    ProtocolObservation,
    build_protocol_observation,
    observation_from_exception,
)
from .stream_follow import SSEProgressDecoder
from .stream_runtime import current_stream_state
from .utils import get_default_model, get_default_user_agent, get_session_token


class ExplicitAsyncWriteRejection(RuntimeError):
    """Raised when the async HTTP mutation probe fails before or with an explicit rejection."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        status: int | None = None,
        observations: tuple[ProtocolObservation, ...] = (),
    ) -> None:
        self.stage = stage
        self.status = status
        self.observations = observations
        super().__init__(message)


class AmbiguousAsyncWriteError(RuntimeError):
    """Raised when an async HTTP POST was dispatched and its commit outcome is uncertain."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        observations: tuple[ProtocolObservation, ...] = (),
        stream_evidence: Mapping[str, Any] | None = None,
        transport_timeout: bool = False,
        expected_user_message_id: str | None = None,
    ) -> None:
        self.status = status
        self.observations = observations
        self.stream_evidence = dict(stream_evidence or {})
        self.transport_timeout = transport_timeout
        self.expected_user_message_id = expected_user_message_id
        super().__init__(message)


@dataclass(frozen=True)
class AsyncMutationProbeResult:
    conversation_id: str
    resolved_thread_id: str
    transport: str
    state: str
    status: int | None
    parent_message_id: str | None
    user_message_id: str | None
    assistant_preview_observed: bool
    attachment_ids: tuple[str, ...] = field(default_factory=tuple)
    observations: tuple[ProtocolObservation, ...] = field(default_factory=tuple)
    stage_times: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "resolved_thread_id": self.resolved_thread_id,
            "transport": self.transport,
            "state": self.state,
            "status": self.status,
            "parent_message_id": self.parent_message_id,
            "user_message_id": self.user_message_id,
            "assistant_preview_observed": self.assistant_preview_observed,
            "attachment_ids": list(self.attachment_ids),
            "protocol_observations": [obs.to_dict() for obs in self.observations],
            "stage_times": dict(self.stage_times),
        }


def _session_cookies(session: Any) -> Any:
    return getattr(session, "cookies", None)


def _coerce_local_attachments(
    attachments: Sequence[LocalAttachment | Path | str] = (),
) -> list[LocalAttachment]:
    result: list[LocalAttachment] = []
    for item in attachments:
        if isinstance(item, LocalAttachment):
            result.append(item)
        else:
            result.append(LocalAttachment.from_path(item))
    return result


def _parse_process_upload_stream(
    chunks: Sequence[bytes | str],
    fallback_text: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    raw_items: list[bytes | str] = list(chunks)
    if not raw_items and fallback_text:
        raw_items.append(fallback_text)
    ready = False
    extra: dict[str, Any] = {}
    for chunk in raw_items:
        text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if not isinstance(parsed, Mapping):
                continue
            event = str(parsed.get("event") or "").strip()
            status = str(parsed.get("status") or "").strip().lower()
            if event == "file.processing.failed" or status == "error" or parsed.get("error"):
                return False, {}
            if (
                event in {"file.processing.completed", "file.processing.file_ready"}
                or status in {"success", "completed", "ready"}
            ):
                ready = True
                raw_extra = parsed.get("extra")
                if isinstance(raw_extra, Mapping):
                    extra.update(raw_extra)
    return ready, extra


def _attach_metadata_to_payload(
    payload: dict[str, Any],
    attachment_metadata: Sequence[Mapping[str, Any]],
) -> None:
    if not attachment_metadata:
        return
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], Mapping):
        return
    first_msg = dict(messages[0])
    meta = dict(first_msg.get("metadata") or {}) if isinstance(first_msg.get("metadata"), Mapping) else {}
    meta["attachments"] = [dict(item) for item in attachment_metadata]
    first_msg["metadata"] = meta
    messages[0] = first_msg


def _upgrade_payload_for_web_frontend(
    base_payload: Mapping[str, Any],
    *,
    conversation_id: str,
    prompt: str,
    snapshot: BrowserSessionSnapshot,
    attachment_metadata: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    raw_messages = base_payload.get("messages")
    first_msg = dict(raw_messages[0]) if isinstance(raw_messages, list) and raw_messages and isinstance(raw_messages[0], Mapping) else {}
    msg_id = str(first_msg.get("id") or uuid.uuid4())
    now = float(first_msg.get("create_time") or time.time())
    parent_id = str(base_payload.get("parent_message_id") or uuid.uuid4())
    model_slug = str(base_payload.get("model") or "gpt-6-thinking")
    msg_metadata: dict[str, Any] = {}
    if attachment_metadata:
        msg_metadata["attachments"] = [dict(item) for item in attachment_metadata]
    return {
        "action": str(base_payload.get("action") or "next"),
        "client_contextual_info": snapshot.client_contextual_info(),
        "client_prepare_state": "sent",
        "conversation_id": conversation_id,
        "genui_state_snapshots": [],
        "is_do_not_remember": False,
        "local_function_names": ["local.continue_in_work"],
        "messages": [
            {
                "id": msg_id,
                "author": {"role": "user", "name": None, "metadata": {}},
                "create_time": now,
                "update_time": None,
                "content": {"content_type": "text", "parts": [prompt]},
                "status": "finished_successfully",
                "end_turn": True,
                "weight": 1.0,
                "metadata": msg_metadata,
                "recipient": "all",
                "channel": None,
            }
        ],
        "model": model_slug,
        "parent_message_id": parent_id,
        "supported_encodings": ["v1"],
        "timezone": snapshot.timezone(),
        "timezone_offset_min": snapshot.timezone_offset_min(),
        "turn_attribution": {"turn_trigger": "composer"},
    }


def _build_payload_from_canonical_graph(
    graph: Mapping[str, Any],
    *,
    conversation_id: str,
    prompt: str,
    fallback_model: str,
) -> dict[str, Any]:
    """Build the minimal graph-addressed continuation payload from one exact read."""

    mapping = graph.get("mapping")
    parent_id = str(graph.get("current_node") or "").strip()
    if not isinstance(mapping, Mapping) or not parent_id or parent_id not in mapping:
        raise ValueError("browser exact read has no canonical current node")
    return {
        "action": "next",
        "conversation_id": conversation_id,
        "parent_message_id": parent_id,
        "model": str(graph.get("default_model_slug") or fallback_model),
        "messages": [{
            "id": str(uuid.uuid4()),
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": [prompt]},
        }],
    }
async def _stage_attachments_async(
    client: Any,
    local_attachments: Sequence[LocalAttachment],
    *,
    conversation_id: str,
    snapshot: BrowserSessionSnapshot | None,
    observations: list[ProtocolObservation],
    seq_start: int,
) -> tuple[list[dict[str, Any]], list[str], int]:
    staged_metadata: list[dict[str, Any]] = []
    staged_file_ids: list[str] = []
    seq = seq_start
    tz_offset = snapshot.timezone_offset_min() if snapshot is not None else 0

    for att in local_attachments:
        mime_type = att.mime_type or "application/octet-stream"
        api_headers = dict(client.build_request_headers())
        api_headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}"
        create_payload = {
            "file_name": att.filename,
            "file_size": att.size,
            "use_case": "my_files",
            "mime_type": mime_type,
            "client_resolved_mime_type": mime_type,
            "mime_resolution_source": "browser",
            "entry_surface": "composer",
            "library_persistence_mode": "opportunistic",
            "reset_rate_limits": False,
            "store_in_library": True,
            "supports_direct_azure_multipart": True,
            "timezone_offset_min": tz_offset,
        }

        seq += 1
        create_started = time.perf_counter()
        try:
            create_resp = await client.session.post(
                url=CHATGPT_API.format("files"),
                headers=api_headers,
                json=create_payload,
            )
        except Exception as exc:
            obs = observation_from_exception(
                exc,
                transport="async",
                operation="continue",
                method="POST",
                path="/backend-api/files",
                headers=api_headers,
                cookies=_session_cookies(client.session),
                payload=create_payload,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - create_started) * 1000.0,
                bootstrap_stage="attachment_staging",
            )
            observations.append(obs)
            raise ExplicitAsyncWriteRejection(
                f"async attachment staging failed: {exc.__class__.__name__}",
                stage="attachment_staging",
                status=obs.status,
                observations=tuple(observations),
            ) from exc

        create_status = int(getattr(create_resp, "status_code", 200) or 200)
        observations.append(
            build_protocol_observation(
                transport="async",
                operation="continue",
                method="POST",
                path="/backend-api/files",
                status=create_status,
                response_class="json_ok" if create_status == 200 else f"http_{create_status}",
                headers=api_headers,
                cookies=_session_cookies(client.session),
                payload=create_payload,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - create_started) * 1000.0,
                bootstrap_stage="attachment_staging",
            )
        )
        if create_status != 200:
            raise ExplicitAsyncWriteRejection(
                f"async attachment staging rejected with HTTP {create_status}",
                stage="attachment_staging",
                status=create_status,
                observations=tuple(observations),
            )

        try:
            create_data = create_resp.json() if callable(getattr(create_resp, "json", None)) else {}
        except Exception as exc:
            raise ExplicitAsyncWriteRejection(
                "async attachment staging returned invalid JSON",
                stage="attachment_staging",
                status=create_status,
                observations=tuple(observations),
            ) from exc

        file_id = str(create_data.get("file_id") or "").strip() if isinstance(create_data, Mapping) else ""
        upload_url = str(create_data.get("upload_url") or "").strip() if isinstance(create_data, Mapping) else ""
        if not file_id or not upload_url:
            raise ExplicitAsyncWriteRejection(
                "async attachment staging missing file_id or upload_url",
                stage="attachment_staging",
                status=create_status,
                observations=tuple(observations),
            )

        seq += 1
        put_started = time.perf_counter()
        raw_bytes = att.path.read_bytes()
        put_headers = {
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2020-04-08",
            "Content-Type": mime_type,
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
        }
        try:
            put_resp = await client.session.put(
                url=upload_url,
                headers=put_headers,
                data=raw_bytes,
            )
        except Exception as exc:
            obs = observation_from_exception(
                exc,
                transport="async",
                operation="continue",
                method="PUT",
                path="/files/upload",
                headers=put_headers,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - put_started) * 1000.0,
                bootstrap_stage="attachment_upload",
            )
            observations.append(obs)
            raise ExplicitAsyncWriteRejection(
                f"async attachment blob upload failed: {exc.__class__.__name__}",
                stage="attachment_upload",
                status=obs.status,
                observations=tuple(observations),
            ) from exc

        put_status = int(getattr(put_resp, "status_code", 200) or 200)
        observations.append(
            build_protocol_observation(
                transport="async",
                operation="continue",
                method="PUT",
                path="/files/upload",
                status=put_status,
                response_class="blob_uploaded" if 200 <= put_status < 300 else f"http_{put_status}",
                headers=put_headers,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - put_started) * 1000.0,
                bootstrap_stage="attachment_upload",
            )
        )
        if not (200 <= put_status < 300):
            raise ExplicitAsyncWriteRejection(
                f"async attachment blob upload rejected with HTTP {put_status}",
                stage="attachment_upload",
                status=put_status,
                observations=tuple(observations),
            )

        seq += 1
        proc_started = time.perf_counter()
        process_payload = {
            "file_id": file_id,
            "use_case": "my_files",
            "index_for_retrieval": False,
            "file_name": att.filename,
            "mime_type": mime_type,
            "entry_surface": "composer",
            "library_persistence_mode": "opportunistic",
            "metadata": None,
        }
        proc_chunks: list[bytes | str] = []

        def proc_callback(chunk: bytes | str) -> None:
            proc_chunks.append(chunk)

        try:
            proc_resp = await client.session.post(
                url=CHATGPT_API.format("files/process_upload_stream"),
                headers=api_headers,
                json=process_payload,
                content_callback=proc_callback,
            )
        except Exception as exc:
            obs = observation_from_exception(
                exc,
                transport="async",
                operation="continue",
                method="POST",
                path="/backend-api/files/process_upload_stream",
                headers=api_headers,
                cookies=_session_cookies(client.session),
                payload=process_payload,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - proc_started) * 1000.0,
                bootstrap_stage="attachment_process",
            )
            observations.append(obs)
            raise ExplicitAsyncWriteRejection(
                f"async attachment processing failed: {exc.__class__.__name__}",
                stage="attachment_process",
                status=obs.status,
                observations=tuple(observations),
            ) from exc

        proc_status = int(getattr(proc_resp, "status_code", 200) or 200)
        ready, extra = _parse_process_upload_stream(
            proc_chunks,
            getattr(proc_resp, "text", None),
        )
        observations.append(
            build_protocol_observation(
                transport="async",
                operation="continue",
                method="POST",
                path="/backend-api/files/process_upload_stream",
                status=proc_status,
                response_class="sse_stream" if proc_status == 200 and ready else f"http_{proc_status}",
                headers=api_headers,
                cookies=_session_cookies(client.session),
                payload=process_payload,
                sequence_index=seq,
                elapsed_ms=(time.perf_counter() - proc_started) * 1000.0,
                bootstrap_stage="attachment_process",
            )
        )
        if proc_status != 200 or not ready:
            raise ExplicitAsyncWriteRejection(
                f"async attachment processing rejected (HTTP {proc_status}, ready={ready})",
                stage="attachment_process",
                status=proc_status,
                observations=tuple(observations),
            )

        att_meta: dict[str, Any] = {
            "id": file_id,
            "name": att.filename,
            "size": att.size,
            "mime_type": mime_type,
        }
        for extra_key in ("library_persistence_result", "library_persistence_reason"):
            if extra.get(extra_key) is not None:
                att_meta[extra_key] = extra[extra_key]
        staged_metadata.append(att_meta)
        staged_file_ids.append(file_id)

    return staged_metadata, staged_file_ids, seq


async def probe_async_continue_once(
    conversation_id: str,
    prompt: str,
    *,
    attachments: Sequence[LocalAttachment | Path | str] = (),
    session_token: str | None = None,
    model: str | None = None,
    user_agent: str | None = None,
    browser_session: BrowserSessionSnapshot | None = None,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 30,
    allow_browser_bootstrap: bool = True,
    client_factory: Callable[..., Any] = AsyncChatGPT,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncMutationProbeResult:
    """Perform at most one async HTTP conversation POST and return redacted evidence."""
    conversation_id = str(conversation_id or "").strip()
    prompt = str(prompt or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id must be provided")
    if not prompt:
        raise ValueError("prompt is empty")
    local_attachments = _coerce_local_attachments(attachments)

    token = session_token
    if token is None:
        try:
            token = get_session_token()
        except Exception:
            token = None
    default_model = model or get_default_model() or "gpt-4"
    agent = user_agent or get_default_user_agent()

    observations: list[ProtocolObservation] = []
    stage_times: dict[str, float] = {"probe_start": time.time()}
    seq = 0
    post_dispatched = False

    snapshot = browser_session
    if snapshot is None and (profile_dir is not None or client_factory is AsyncChatGPT):
        snapshot = load_browser_session_snapshot(profile_dir=profile_dir)
    if snapshot is not None and not snapshot.has_fresh_sentinel():
        try:
            refreshed = await refresh_browser_session_sentinel_async(
                snapshot,
                profile_dir=profile_dir,
                cdp_endpoint=cdp_endpoint,
            )
            if refreshed is not None and refreshed.has_fresh_sentinel():
                snapshot = refreshed
        except Exception:
            pass
    if (
        (snapshot is None or not snapshot.has_fresh_sentinel())
        and allow_browser_bootstrap
        and client_factory is AsyncChatGPT
    ):
        try:
            snapshot = await capture_browser_session_snapshot_async(
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                timeout=browser_timeout,
                save=True,
            )
        except Exception:
            pass

    factory_kwargs: dict[str, Any] = {
        "session_token": token,
        "default_model": default_model,
        "user_agent": agent,
    }
    if snapshot is not None:
        try:
            sig = inspect.signature(client_factory)
            if "browser_session" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                factory_kwargs["browser_session"] = snapshot
        except (TypeError, ValueError):
            pass

    try:
        async with client_factory(**factory_kwargs) as client:
            if snapshot is not None and getattr(client, "_replayed_browser_snapshot", None) is None:
                apply_browser_session_to_client(client, snapshot)

            stage_times["authenticated"] = time.time()
            conv = client.get_conversation(conversation_id)

            seq += 1
            build_started = time.perf_counter()
            read_transport = "async"
            try:
                payload = await conv.build_message_payload(prompt)
            except Exception as exc:
                obs = observation_from_exception(
                    exc,
                    transport="async",
                    operation="continue",
                    method="GET",
                    path=f"/backend-api/conversation/{conversation_id}",
                    headers=client.build_request_headers(),
                    cookies=_session_cookies(client.session),
                    sequence_index=seq,
                    elapsed_ms=(time.perf_counter() - build_started) * 1000.0,
                    bootstrap_stage="conversation_get",
                )
                observations.append(obs)
                # A direct request-context rejection is still pre-commit.  An
                # explicitly attached browser may supply one exact graph read;
                # it never submits the prompt or authorizes a DOM fallback.
                if snapshot is None or not cdp_endpoint or obs.status != 429:
                    raise ExplicitAsyncWriteRejection(
                        f"async precommit conversation build failed: {exc.__class__.__name__}",
                        stage="conversation_get",
                        status=obs.status,
                        observations=tuple(observations),
                    ) from exc
                try:
                    browser_read = fetch_conversation_same_origin(
                        conversation_id,
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        timeout=browser_timeout,
                    )
                    payload = _build_payload_from_canonical_graph(
                        browser_read.chat,
                        conversation_id=conversation_id,
                        prompt=prompt,
                        fallback_model=default_model,
                    )
                    read_transport = "browser"
                    diagnostic = dict(browser_read.diagnostic or {})
                    seq += 1
                    observations.append(build_protocol_observation(
                        transport="browser", operation="continue", method="GET",
                        path="/backend-api/conversation/<id>",
                        status=int(diagnostic.get("status") or 200),
                        response_class=str(diagnostic.get("response_class") or "json_ok"),
                        headers={}, sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - build_started) * 1000.0,
                        bootstrap_stage="conversation_get",
                    ))
                except Exception:
                    raise ExplicitAsyncWriteRejection(
                        f"async precommit conversation build failed: {exc.__class__.__name__}",
                        stage="conversation_get",
                        status=obs.status,
                        observations=tuple(observations),
                    ) from exc

            stage_times["hydrated"] = time.time()
            if read_transport == "async":
                observations.append(
                    build_protocol_observation(
                        transport="async",
                        operation="continue",
                        method="GET",
                        path=f"/backend-api/conversation/{conversation_id}",
                        status=200,
                        response_class="json_ok",
                        headers={
                            k: v
                            for k, v in client.build_request_headers().items()
                            if k.lower() != "content-type"
                        }
                        | {"Accept": "application/json"},
                        cookies=_session_cookies(client.session),
                        sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - build_started) * 1000.0,
                        bootstrap_stage="conversation_get",
                    )
                )

            staged_metadata: list[dict[str, Any]] = []
            staged_file_ids: list[str] = []
            if local_attachments:
                staged_metadata, staged_file_ids, seq = await _stage_attachments_async(
                    client,
                    local_attachments,
                    conversation_id=conversation_id,
                    snapshot=snapshot,
                    observations=observations,
                    seq_start=seq,
                )
                stage_times["attachment_ready"] = time.time()

            seq += 1
            sentinel_started = time.perf_counter()
            headers = dict(client.build_request_headers())
            if client_factory is AsyncChatGPT:
                try:
                    from .proof_of_work import mint_sentinel_tokens_cffi

                    cffi_sentinel = await mint_sentinel_tokens_cffi(
                        client.session,
                        headers,
                        user_agent=agent,
                    )
                    if cffi_sentinel.get("chat_requirements_token"):
                        if snapshot is None:
                            snapshot = BrowserSessionSnapshot(
                                captured_at=time.time(),
                                user_agent=agent,
                                sentinel=cffi_sentinel,
                            )
                        else:
                            snapshot.sentinel = cffi_sentinel
                except Exception:
                    if snapshot is not None and not snapshot.has_fresh_sentinel():
                        try:
                            refreshed = await refresh_browser_session_sentinel_async(
                                snapshot,
                                profile_dir=profile_dir,
                                cdp_endpoint=cdp_endpoint,
                            )
                            if refreshed is not None and refreshed.has_fresh_sentinel():
                                snapshot = refreshed
                        except Exception:
                            pass
            use_replayed_sentinel = snapshot is not None and snapshot.has_fresh_sentinel()
            if use_replayed_sentinel:
                assert snapshot is not None
                payload = _upgrade_payload_for_web_frontend(
                    payload,
                    conversation_id=conversation_id,
                    prompt=prompt,
                    snapshot=snapshot,
                    attachment_metadata=staged_metadata,
                )
                trace_id = str(uuid.uuid4())
                headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}"
                headers["x-oai-mcp-form-version"] = "2"
                headers["x-oai-turn-trace-id"] = trace_id
                headers.update(snapshot.sentinel_headers())
                observations.append(
                    build_protocol_observation(
                        transport="async",
                        operation="continue",
                        method="POST",
                        path="/backend-api/sentinel/chat-requirements/finalize",
                        status=200,
                        response_class="json_ok",
                        headers=headers,
                        cookies=_session_cookies(client.session),
                        sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                        bootstrap_stage="sentinel_finalize",
                    )
                )
                mark_sentinel_consumed(snapshot, profile_dir=profile_dir)
                if client_factory is AsyncChatGPT:
                    from .proof_of_work import prepare_conduit_cffi

                    conduit_token = await prepare_conduit_cffi(
                        client.session,
                        headers,
                        conversation_id=conversation_id,
                        parent_message_id=str(payload.get("parent_message_id") or "") if isinstance(payload, Mapping) else None,
                        model=str((payload.get("model") if isinstance(payload, Mapping) else None) or default_model),
                        action=str((payload.get("action") if isinstance(payload, Mapping) else None) or "next"),
                        trace_id=trace_id,
                    )
                    if conduit_token:
                        headers["x-conduit-token"] = conduit_token
                    url = CHATGPT_API.format("f/conversation")
                else:
                    url = CHATGPT_API.format("conversation")
            else:
                if staged_metadata and isinstance(payload, dict):
                    _attach_metadata_to_payload(payload, staged_metadata)
                try:
                    sentinel_token = await client.create_chat_requirements_token()
                except Exception as exc:
                    obs = observation_from_exception(
                        exc,
                        transport="async",
                        operation="continue",
                        method="POST",
                        path="/backend-api/sentinel/chat-requirements",
                        headers=headers,
                        cookies=_session_cookies(client.session),
                        sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                        bootstrap_stage="sentinel_requirements",
                    )
                    observations.append(obs)
                    raise ExplicitAsyncWriteRejection(
                        f"async precommit sentinel failed: {exc.__class__.__name__}",
                        stage="sentinel_requirements",
                        status=obs.status,
                        observations=tuple(observations),
                    ) from exc

                observations.append(
                    build_protocol_observation(
                        transport="async",
                        operation="continue",
                        method="POST",
                        path="/backend-api/sentinel/chat-requirements",
                        status=200,
                        response_class="json_ok",
                        headers=headers,
                        cookies=_session_cookies(client.session),
                        sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                        bootstrap_stage="sentinel_requirements",
                    )
                )
                if sentinel_token:
                    headers["openai-sentinel-chat-requirements-token"] = sentinel_token
                url = CHATGPT_API.format("conversation")

            seq += 1
            post_started = time.perf_counter()
            chunks: list[bytes | str] = []
            decoder = SSEProgressDecoder()

            def content_callback(chunk: bytes | str) -> None:
                # Independent receipt decoder; pacing owns progress callback delivery.
                events = decoder.feed(chunk)
                chunks.append(chunk)
                if progress_callback is not None and current_stream_state() is None:
                    for event in events:
                        progress_callback(dict(event))

            try:
                post_dispatched = True
                response = await client.session.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    content_callback=content_callback,
                )
            except Exception as exc:
                obs = observation_from_exception(
                    exc,
                    transport="async",
                    operation="continue",
                    method="POST",
                    path=url,
                    headers=headers,
                    cookies=_session_cookies(client.session),
                    payload=payload,
                    sse_chunks=chunks,
                    sequence_index=seq,
                    elapsed_ms=(time.perf_counter() - post_started) * 1000.0,
                    bootstrap_stage="conversation_post",
                )
                observations.append(obs)
                raise AmbiguousAsyncWriteError(
                    f"async conversation POST outcome is ambiguous ({exc.__class__.__name__}); refusing retry or fallback",
                    status=obs.status,
                    observations=tuple(observations),
                    stream_evidence={
                        "stream_terminal_observed": decoder.terminal_observed,
                        "stream_terminal_reason": decoder.terminal_reason,
                        "stream_event_counts": decoder.event_counts,
                    },
                    transport_timeout=isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower(),
                    expected_user_message_id=str(payload["messages"][0]["id"]) if payload.get("messages") else None,
                ) from exc

            status = int(getattr(response, "status_code", 200) or 200)
            elapsed_ms = (time.perf_counter() - post_started) * 1000.0
            if not chunks and getattr(response, "text", None):
                chunks.append(str(response.text))

            post_obs = build_protocol_observation(
                transport="async",
                operation="continue",
                method="POST",
                path=url,
                status=status,
                response_class="sse_stream" if status == 200 else f"http_{status}",
                headers=headers,
                cookies=_session_cookies(client.session),
                payload=payload,
                sse_chunks=chunks if status == 200 else None,
                sequence_index=seq,
                elapsed_ms=elapsed_ms,
                bootstrap_stage="conversation_post",
            )
            observations.append(post_obs)

            if 400 <= status < 500:
                raise ExplicitAsyncWriteRejection(
                    f"async conversation POST rejected with HTTP {status}",
                    stage="conversation_post",
                    status=status,
                    observations=tuple(observations),
                )
            if status >= 500:
                raise AmbiguousAsyncWriteError(
                    f"async conversation POST returned HTTP {status}; outcome is ambiguous",
                    status=status,
                    observations=tuple(observations),
                )

            sse_shape = post_obs.sse_shape or {}
            if int(sse_shape.get("data_event_count") or 0) == 0:
                raise AmbiguousAsyncWriteError(
                    "async conversation POST returned HTTP 200 without SSE data events; outcome is ambiguous",
                    status=status,
                    observations=tuple(observations),
                )

            stage_times["submission_observed"] = time.time()
            messages = payload.get("messages") if isinstance(payload, Mapping) else None
            first_msg = messages[0] if isinstance(messages, list) and messages else {}
            user_message_id = str(first_msg.get("id")) if isinstance(first_msg.get("id") if isinstance(first_msg, Mapping) else None, str) else None
            parent_message_id = str(payload.get("parent_message_id")) if isinstance(payload, Mapping) and payload.get("parent_message_id") else None

            return AsyncMutationProbeResult(
                conversation_id=conversation_id,
                resolved_thread_id=conversation_id,
                transport="async",
                state="submission_observed",
                status=status,
                parent_message_id=parent_message_id,
                user_message_id=user_message_id,
                assistant_preview_observed=bool(sse_shape.get("assistant_message_events") or sse_shape.get("done_seen")),
                attachment_ids=tuple(staged_file_ids),
                observations=tuple(observations),
                stage_times=stage_times,
            )
    except (ExplicitAsyncWriteRejection, AmbiguousAsyncWriteError):
        raise
    except Exception as exc:
        if post_dispatched:
            raise AmbiguousAsyncWriteError(
                f"async conversation POST dispatched; post-dispatch failure {exc.__class__.__name__}",
                observations=tuple(observations),
            ) from exc
        seq += 1
        obs = observation_from_exception(
            exc,
            transport="async",
            operation="continue",
            method="GET",
            path="/api/auth/session",
            sequence_index=seq,
            bootstrap_stage="auth_session",
        )
        observations.append(obs)
        raise ExplicitAsyncWriteRejection(
            f"async client bootstrap failed: {exc.__class__.__name__}",
            stage="auth_session",
            status=obs.status,
            observations=tuple(observations),
        ) from exc


def submit_async_prompt_once(
    conversation_id: str,
    prompt: str,
    *,
    attachments: Sequence[LocalAttachment | Path | str] = (),
    session_token: str | None = None,
    model: str | None = None,
    user_agent: str | None = None,
    browser_session: BrowserSessionSnapshot | None = None,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 30,
    allow_browser_bootstrap: bool = True,
    client_factory: Callable[..., Any] = AsyncChatGPT,
    verify_callback: Callable[[str, str], dict[str, Any]] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Synchronous wrapper for one-attempt async HTTP mutation probe."""
    result = run_async_sync(
        lambda: probe_async_continue_once(
            conversation_id,
            prompt,
            attachments=attachments,
            session_token=session_token,
            model=model,
            user_agent=user_agent,
            browser_session=browser_session,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            allow_browser_bootstrap=allow_browser_bootstrap,
            client_factory=client_factory,
            progress_callback=progress_callback,
        )
    )
    payload = result.to_dict()
    if verify_callback is not None:
        payload["verification"] = verify_callback(conversation_id, prompt)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one opt-in async-CFFI mutation probe against a sacrificial thread"
    )
    parser.add_argument("conversation_id")
    parser.add_argument("--prompt", required=True)
    parser.add_argument(
        "--attach",
        action="append",
        type=Path,
        default=[],
        help="Attach a file via direct HTTP staging; repeat for multiple files",
    )
    parser.add_argument(
        "--sacrificial",
        action="store_true",
        required=True,
        help="Required safety acknowledgement that the target thread is sacrificial",
    )
    parser.add_argument("--key")
    parser.add_argument("--model")
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=30)
    parser.add_argument("--no-browser-bootstrap", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = submit_async_prompt_once(
            args.conversation_id,
            args.prompt,
            attachments=args.attach,
            session_token=args.key,
            model=args.model,
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            browser_timeout=args.browser_timeout,
            allow_browser_bootstrap=not args.no_browser_bootstrap,
        )
    except KeyboardInterrupt:
        return 130
    except ExplicitAsyncWriteRejection as exc:
        print(json.dumps({
            "state": "rejected_precommit",
            "stage": exc.stage,
            "status": exc.status,
            "protocol_observations": [obs.to_dict() for obs in exc.observations],
        }, indent=2, sort_keys=True))
        return 2
    except AmbiguousAsyncWriteError as exc:
        print(json.dumps({
            "state": "ambiguous_post",
            "status": exc.status,
            "protocol_observations": [obs.to_dict() for obs in exc.observations],
        }, indent=2, sort_keys=True))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
