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
import asyncio
from dataclasses import dataclass, field
import inspect
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Callable, Mapping

from .async_chatgpt import AsyncChatGPT, CHATGPT_API
from .browser_session_replay import (
    BrowserSessionSnapshot,
    apply_browser_session_to_client,
    capture_browser_session_snapshot_async,
    load_browser_session_snapshot,
    mark_sentinel_consumed,
)
from .protocol_observation import (
    ProtocolObservation,
    build_protocol_observation,
    observation_from_exception,
)
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
    ) -> None:
        self.status = status
        self.observations = observations
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
            "protocol_observations": [obs.to_dict() for obs in self.observations],
            "stage_times": dict(self.stage_times),
        }


def _session_cookies(session: Any) -> Any:
    return getattr(session, "cookies", None)


def _upgrade_payload_for_web_frontend(
    base_payload: Mapping[str, Any],
    *,
    conversation_id: str,
    prompt: str,
    snapshot: BrowserSessionSnapshot,
) -> dict[str, Any]:
    raw_messages = base_payload.get("messages")
    first_msg = dict(raw_messages[0]) if isinstance(raw_messages, list) and raw_messages and isinstance(raw_messages[0], Mapping) else {}
    msg_id = str(first_msg.get("id") or uuid.uuid4())
    now = float(first_msg.get("create_time") or time.time())
    parent_id = str(base_payload.get("parent_message_id") or uuid.uuid4())
    model_slug = str(base_payload.get("model") or "gpt-6-thinking")
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
                "metadata": {},
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


async def probe_async_continue_once(
    conversation_id: str,
    prompt: str,
    *,
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
) -> AsyncMutationProbeResult:
    """Perform at most one async HTTP conversation POST and return redacted evidence."""
    conversation_id = str(conversation_id or "").strip()
    prompt = str(prompt or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id must be provided")
    if not prompt:
        raise ValueError("prompt is empty")

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

            # Stage 1: build payload (fetches parent conversation graph if needed)
            seq += 1
            build_started = time.perf_counter()
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
                raise ExplicitAsyncWriteRejection(
                    f"async precommit conversation build failed: {exc.__class__.__name__}",
                    stage="conversation_get",
                    status=obs.status,
                    observations=tuple(observations),
                ) from exc

            stage_times["hydrated"] = time.time()
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

            # Stage 2: sentinel requirements token (replayed from browser session if fresh, else direct)
            seq += 1
            sentinel_started = time.perf_counter()
            headers = dict(client.build_request_headers())
            use_replayed_sentinel = snapshot is not None and snapshot.has_fresh_sentinel()
            if use_replayed_sentinel:
                assert snapshot is not None
                payload = _upgrade_payload_for_web_frontend(
                    payload,
                    conversation_id=conversation_id,
                    prompt=prompt,
                    snapshot=snapshot,
                )
                headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}"
                headers["x-oai-mcp-form-version"] = "2"
                headers["x-oai-turn-trace-id"] = str(uuid.uuid4())
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
                url = CHATGPT_API.format("f/conversation")
            else:
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

            # Stage 3: single-attempt conversation POST
            seq += 1
            post_started = time.perf_counter()
            chunks: list[bytes | str] = []

            def content_callback(chunk: bytes | str) -> None:
                chunks.append(chunk)

            try:
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

            if status >= 400:
                raise ExplicitAsyncWriteRejection(
                    f"async conversation POST rejected with HTTP {status}",
                    stage="conversation_post",
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
                observations=tuple(observations),
                stage_times=stage_times,
            )
    except (ExplicitAsyncWriteRejection, AmbiguousAsyncWriteError):
        raise
    except Exception as exc:
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
) -> dict[str, Any]:
    """Synchronous wrapper for one-attempt async HTTP mutation probe."""
    result = asyncio.run(
        probe_async_continue_once(
            conversation_id,
            prompt,
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = submit_async_prompt_once(
            args.conversation_id,
            args.prompt,
            session_token=args.key,
            model=args.model,
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
        )
    except AmbiguousAsyncWriteError as exc:
        print(
            json.dumps(
                {
                    "state": "commit_unknown",
                    "error_class": exc.__class__.__name__,
                    "status": exc.status,
                    "protocol_observations": [obs.to_dict() for obs in exc.observations],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    except ExplicitAsyncWriteRejection as exc:
        print(
            json.dumps(
                {
                    "state": "failed_precommit",
                    "stage": exc.stage,
                    "error_class": exc.__class__.__name__,
                    "status": exc.status,
                    "protocol_observations": [obs.to_dict() for obs in exc.observations],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
