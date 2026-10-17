"""Exactly-once direct async graph mutations above the ChatGPT conversation API.

The module owns the transport mechanics for graph-structural operations that are
not ordinary tail continuation: new conversation, branch-from-node, regenerate,
and edit-and-branch.  Canonical success is still decided later from an exact
conversation graph by :mod:`re_gpt.mutation_service`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import json
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

from .async_bridge import run_async_sync
from .async_chatgpt import AsyncChatGPT, CHATGPT_API
from .async_mutation_probe import (
    AmbiguousAsyncWriteError,
    ExplicitAsyncWriteRejection,
    _session_cookies,
    _stage_attachments_async,
)
from .attachments import LocalAttachment
from .browser_session_replay import (
    BrowserSessionSnapshot,
    apply_browser_session_to_client,
    capture_browser_session_snapshot_async,
    load_browser_session_snapshot,
    mark_sentinel_consumed,
)
from .protocol_observation import ProtocolObservation, build_protocol_observation, observation_from_exception
from .utils import get_default_model, get_default_user_agent, get_session_token

GRAPH_MUTATIONS = frozenset({"new_conversation", "branch", "regenerate", "edit_branch"})


@dataclass(frozen=True)
class DirectGraphMutationResult:
    operation: str
    conversation_id: str | None
    resolved_thread_id: str | None
    transport: str
    state: str
    status: int | None
    parent_message_id: str | None
    user_message_id: str | None
    assistant_message_id: str | None
    attachment_ids: tuple[str, ...] = field(default_factory=tuple)
    observations: tuple[ProtocolObservation, ...] = field(default_factory=tuple)
    stage_times: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "conversation_id": self.conversation_id,
            "resolved_thread_id": self.resolved_thread_id,
            "transport": self.transport,
            "state": self.state,
            "status": self.status,
            "parent_message_id": self.parent_message_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "attachment_ids": list(self.attachment_ids),
            "protocol_observations": [item.to_dict() for item in self.observations],
            "stage_times": dict(self.stage_times),
        }


def _coerce_attachments(values: Sequence[LocalAttachment | Path | str]) -> list[LocalAttachment]:
    return [item if isinstance(item, LocalAttachment) else LocalAttachment.from_path(item) for item in values]


def build_graph_mutation_payload(
    base_payload: Mapping[str, Any],
    *,
    operation: str,
    conversation_id: str | None,
    parent_node_id: str | None,
    prompt: str | None,
    attachment_metadata: Sequence[Mapping[str, Any]] = (),
    snapshot: BrowserSessionSnapshot | None = None,
) -> dict[str, Any]:
    """Shape one protocol payload without guessing graph coordinates.

    ``branch`` and ``edit_branch`` are both ordinary ``next`` actions from an
    explicit parent node. ``regenerate`` is a ``variant`` action from an exact
    user node and contains no replacement user message.
    """

    if operation not in GRAPH_MUTATIONS:
        raise ValueError(f"unsupported graph mutation: {operation}")
    if operation != "new_conversation" and not str(conversation_id or "").strip():
        raise ValueError(f"{operation} requires conversation_id")
    if operation in {"branch", "regenerate", "edit_branch"} and not str(parent_node_id or "").strip():
        raise ValueError(f"{operation} requires parent_node_id")
    if operation != "regenerate" and not str(prompt or "").strip():
        raise ValueError(f"{operation} requires prompt")

    payload = dict(base_payload)
    if operation == "new_conversation":
        payload.pop("conversation_id", None)
        payload["parent_message_id"] = "client-created-root"
        payload["action"] = "next"
    elif operation == "regenerate":
        payload["conversation_id"] = str(conversation_id)
        payload["parent_message_id"] = str(parent_node_id)
        payload["action"] = "variant"
        payload["variant_purpose"] = "comparison_implicit"
        payload.pop("messages", None)
    else:
        payload["conversation_id"] = str(conversation_id)
        payload["parent_message_id"] = str(parent_node_id)
        payload["action"] = "next"

    if operation != "regenerate":
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], Mapping):
            raise ValueError("next-action payload has no user message")
        first = dict(messages[0])
        content = dict(first.get("content") or {})
        content["content_type"] = str(content.get("content_type") or "text")
        content["parts"] = [str(prompt)]
        first["content"] = content
        metadata = dict(first.get("metadata") or {})
        if attachment_metadata:
            metadata["attachments"] = [dict(item) for item in attachment_metadata]
        first["metadata"] = metadata
        payload["messages"] = [first, *messages[1:]]

    if snapshot is not None:
        # Match the already-live /f/conversation shape while retaining the graph
        # coordinates/action established above. Values here come from the owned
        # authenticated browser session rather than fabricated identity data.
        upgraded: dict[str, Any] = {
            "action": payload["action"],
            "client_contextual_info": snapshot.client_contextual_info(),
            "client_prepare_state": "none" if operation == "regenerate" else "sent",
            "genui_state_snapshots": [],
            "is_do_not_remember": False,
            "local_function_names": ["local.continue_in_work"],
            "model": payload.get("model") or "gpt-6-thinking",
            "parent_message_id": payload["parent_message_id"],
            "supported_encodings": ["v1"],
            "timezone": snapshot.timezone(),
            "timezone_offset_min": snapshot.timezone_offset_min(),
            "turn_attribution": {"turn_trigger": "composer"},
        }
        if conversation_id:
            upgraded["conversation_id"] = str(conversation_id)
        if operation == "regenerate":
            upgraded["variant_purpose"] = "comparison_implicit"
        else:
            upgraded["messages"] = payload["messages"]
        payload = upgraded

    return payload


def extract_sse_identity(chunks: Sequence[bytes | str]) -> dict[str, str | None]:
    """Extract only canonical IDs/roles from SSE; never return message content."""

    conversation_id: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    for chunk in chunks:
        text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                event = json.loads(body)
            except Exception:
                continue
            if not isinstance(event, Mapping):
                continue
            if event.get("conversation_id"):
                conversation_id = str(event["conversation_id"])
            message = event.get("message")
            if not isinstance(message, Mapping) or not message.get("id"):
                continue
            author = message.get("author")
            role = str(author.get("role") or "") if isinstance(author, Mapping) else ""
            if role == "user":
                user_message_id = str(message["id"])
            elif role == "assistant":
                assistant_message_id = str(message["id"])
    return {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


async def probe_direct_graph_mutation_once(
    operation: str,
    *,
    conversation_id: str | None = None,
    parent_node_id: str | None = None,
    prompt: str | None = None,
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
) -> DirectGraphMutationResult:
    if operation not in GRAPH_MUTATIONS:
        raise ValueError(f"unsupported graph mutation: {operation}")
    conversation_id = str(conversation_id or "").strip() or None
    parent_node_id = str(parent_node_id or "").strip() or None
    prompt = str(prompt or "").strip() or None
    local_attachments = _coerce_attachments(attachments)
    if operation in {"new_conversation", "regenerate"} and local_attachments:
        raise ValueError(f"attachments are not supported for {operation}; attach on a subsequent branch/continue")

    token = session_token
    if token is None:
        try:
            token = get_session_token()
        except Exception:
            token = None
    default_model = model or get_default_model() or "gpt-4"
    agent = user_agent or get_default_user_agent()

    snapshot = browser_session
    if snapshot is None and (profile_dir is not None or client_factory is AsyncChatGPT):
        snapshot = load_browser_session_snapshot(profile_dir=profile_dir)
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
            if "browser_session" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                factory_kwargs["browser_session"] = snapshot
        except (TypeError, ValueError):
            pass

    observations: list[ProtocolObservation] = []
    stage_times: dict[str, float] = {"probe_start": time.time()}
    seq = 0
    post_dispatched = False

    try:
        async with client_factory(**factory_kwargs) as client:
            if snapshot is not None and getattr(client, "_replayed_browser_snapshot", None) is None:
                apply_browser_session_to_client(client, snapshot)
            stage_times["authenticated"] = time.time()

            if operation == "new_conversation":
                conv = client.create_new_conversation(model=default_model)
            else:
                conv = client.get_conversation(str(conversation_id))

            seq += 1
            build_started = time.perf_counter()
            try:
                # Reuse the library's model/context shaping. For regenerate the
                # placeholder is deleted before any POST occurs.
                base_payload = await conv.build_message_payload(prompt or "__regenerate_placeholder__")
            except Exception as exc:
                obs = observation_from_exception(
                    exc,
                    transport="async",
                    operation=operation,
                    method="GET" if conversation_id else "POST",
                    path=f"/backend-api/conversation/{conversation_id}" if conversation_id else "/backend-api/conversation/init",
                    headers=client.build_request_headers(),
                    cookies=_session_cookies(client.session),
                    sequence_index=seq,
                    elapsed_ms=(time.perf_counter() - build_started) * 1000.0,
                    bootstrap_stage="conversation_get" if conversation_id else "conversation_init",
                )
                observations.append(obs)
                raise ExplicitAsyncWriteRejection(
                    f"direct {operation} payload build failed: {exc.__class__.__name__}",
                    stage="conversation_get" if conversation_id else "conversation_init",
                    status=obs.status,
                    observations=tuple(observations),
                ) from exc

            if conversation_id:
                observations.append(build_protocol_observation(
                    transport="async",
                    operation=operation,
                    method="GET",
                    path=f"/backend-api/conversation/{conversation_id}",
                    status=200,
                    response_class="json_ok",
                    headers={k: v for k, v in client.build_request_headers().items() if k.lower() != "content-type"} | {"Accept": "application/json"},
                    cookies=_session_cookies(client.session),
                    sequence_index=seq,
                    elapsed_ms=(time.perf_counter() - build_started) * 1000.0,
                    bootstrap_stage="conversation_get",
                ))
            stage_times["hydrated"] = time.time()

            staged_metadata: list[dict[str, Any]] = []
            staged_file_ids: list[str] = []
            if local_attachments:
                staged_metadata, staged_file_ids, seq = await _stage_attachments_async(
                    client,
                    local_attachments,
                    conversation_id=str(conversation_id),
                    snapshot=snapshot,
                    observations=observations,
                    seq_start=seq,
                )
                stage_times["attachment_ready"] = time.time()

            payload = build_graph_mutation_payload(
                base_payload,
                operation=operation,
                conversation_id=conversation_id,
                parent_node_id=parent_node_id,
                prompt=prompt,
                attachment_metadata=staged_metadata,
                snapshot=snapshot if snapshot is not None and snapshot.has_fresh_sentinel() else None,
            )

            headers = dict(client.build_request_headers())
            use_replayed_sentinel = snapshot is not None and snapshot.has_fresh_sentinel()
            seq += 1
            sentinel_started = time.perf_counter()
            if use_replayed_sentinel:
                assert snapshot is not None
                headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}" if conversation_id else "https://chatgpt.com/"
                headers["x-oai-mcp-form-version"] = "2"
                headers["x-oai-turn-trace-id"] = str(uuid.uuid4())
                headers.update(snapshot.sentinel_headers())
                observations.append(build_protocol_observation(
                    transport="async", operation=operation, method="POST",
                    path="/backend-api/sentinel/chat-requirements/finalize", status=200,
                    response_class="json_ok", headers=headers, cookies=_session_cookies(client.session),
                    sequence_index=seq, elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                    bootstrap_stage="sentinel_finalize",
                ))
                mark_sentinel_consumed(snapshot, profile_dir=profile_dir)
                url = CHATGPT_API.format("f/conversation")
            else:
                try:
                    sentinel_token = await client.create_chat_requirements_token()
                except Exception as exc:
                    obs = observation_from_exception(
                        exc, transport="async", operation=operation, method="POST",
                        path="/backend-api/sentinel/chat-requirements", headers=headers,
                        cookies=_session_cookies(client.session), sequence_index=seq,
                        elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                        bootstrap_stage="sentinel_requirements",
                    )
                    observations.append(obs)
                    raise ExplicitAsyncWriteRejection(
                        f"direct {operation} sentinel failed: {exc.__class__.__name__}",
                        stage="sentinel_requirements", status=obs.status, observations=tuple(observations),
                    ) from exc
                if sentinel_token:
                    headers["openai-sentinel-chat-requirements-token"] = sentinel_token
                observations.append(build_protocol_observation(
                    transport="async", operation=operation, method="POST",
                    path="/backend-api/sentinel/chat-requirements", status=200,
                    response_class="json_ok", headers=headers, cookies=_session_cookies(client.session),
                    sequence_index=seq, elapsed_ms=(time.perf_counter() - sentinel_started) * 1000.0,
                    bootstrap_stage="sentinel_requirements",
                ))
                url = CHATGPT_API.format("conversation")

            seq += 1
            post_started = time.perf_counter()
            chunks: list[bytes | str] = []
            try:
                post_dispatched = True
                response = await client.session.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    content_callback=lambda chunk: chunks.append(chunk),
                )
            except Exception as exc:
                obs = observation_from_exception(
                    exc, transport="async", operation=operation, method="POST", path=url,
                    headers=headers, cookies=_session_cookies(client.session), payload=payload,
                    sse_chunks=chunks, sequence_index=seq,
                    elapsed_ms=(time.perf_counter() - post_started) * 1000.0,
                    bootstrap_stage="conversation_post",
                )
                observations.append(obs)
                raise AmbiguousAsyncWriteError(
                    f"direct {operation} POST outcome is ambiguous ({exc.__class__.__name__}); refusing retry or fallback",
                    status=obs.status, observations=tuple(observations),
                ) from exc

            status = int(getattr(response, "status_code", 200) or 200)
            if not chunks and getattr(response, "text", None):
                chunks.append(str(response.text))
            post_obs = build_protocol_observation(
                transport="async", operation=operation, method="POST", path=url,
                status=status, response_class="sse_stream" if status == 200 else f"http_{status}",
                headers=headers, cookies=_session_cookies(client.session), payload=payload,
                sse_chunks=chunks if status == 200 else None, sequence_index=seq,
                elapsed_ms=(time.perf_counter() - post_started) * 1000.0,
                bootstrap_stage="conversation_post",
            )
            observations.append(post_obs)
            if 400 <= status < 500:
                raise ExplicitAsyncWriteRejection(
                    f"direct {operation} rejected with HTTP {status}", stage="conversation_post",
                    status=status, observations=tuple(observations),
                )
            if status >= 500:
                raise AmbiguousAsyncWriteError(
                    f"direct {operation} returned HTTP {status}; outcome is ambiguous",
                    status=status, observations=tuple(observations),
                )
            if int((post_obs.sse_shape or {}).get("data_event_count") or 0) == 0:
                raise AmbiguousAsyncWriteError(
                    f"direct {operation} returned HTTP 200 without SSE data; outcome is ambiguous",
                    status=status, observations=tuple(observations),
                )

            identity = extract_sse_identity(chunks)
            resolved_id = str(identity.get("conversation_id") or conversation_id or "").strip() or None
            if operation == "new_conversation" and not resolved_id:
                raise AmbiguousAsyncWriteError(
                    "new conversation POST succeeded but no conversation id was observed; refusing retry",
                    status=status, observations=tuple(observations),
                )
            messages = payload.get("messages") if isinstance(payload, Mapping) else None
            first_message = messages[0] if isinstance(messages, list) and messages and isinstance(messages[0], Mapping) else {}
            user_message_id = str(first_message.get("id") or identity.get("user_message_id") or "").strip() or None
            stage_times["submission_observed"] = time.time()
            return DirectGraphMutationResult(
                operation=operation,
                conversation_id=conversation_id,
                resolved_thread_id=resolved_id,
                transport="async",
                state="submission_observed",
                status=status,
                parent_message_id=str(payload.get("parent_message_id") or "").strip() or None,
                user_message_id=user_message_id,
                assistant_message_id=str(identity.get("assistant_message_id") or "").strip() or None,
                attachment_ids=tuple(staged_file_ids),
                observations=tuple(observations),
                stage_times=stage_times,
            )
    except (ExplicitAsyncWriteRejection, AmbiguousAsyncWriteError):
        raise
    except Exception as exc:
        if post_dispatched:
            raise AmbiguousAsyncWriteError(
                f"direct {operation} POST dispatched; post-dispatch failure {exc.__class__.__name__}",
                observations=tuple(observations),
            ) from exc
        seq += 1
        obs = observation_from_exception(
            exc, transport="async", operation=operation, method="GET", path="/api/auth/session",
            sequence_index=seq, bootstrap_stage="auth_session",
        )
        observations.append(obs)
        raise ExplicitAsyncWriteRejection(
            f"direct {operation} bootstrap failed: {exc.__class__.__name__}",
            stage="auth_session", status=obs.status, observations=tuple(observations),
        ) from exc


def submit_direct_graph_mutation_once(operation: str, **kwargs: Any) -> dict[str, Any]:
    result = run_async_sync(lambda: probe_direct_graph_mutation_once(operation, **kwargs))
    return result.to_dict()
