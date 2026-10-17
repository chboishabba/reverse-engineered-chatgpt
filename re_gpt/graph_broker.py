"""Adapter-independent exact conversation graph acquisition.

Adapters are acquisition mechanisms only. The first valid graph wins; callers
remain responsible for persistence and mutation-receipt classification.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .async_chatgpt import AsyncChatGPT
from .browser_observer import fetch_conversation_same_origin
from .browser_session_replay import load_browser_session_snapshot
from .protocol_observation import (
    build_protocol_observation,
    observation_from_exception,
)
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token

_STATUS_RE = re.compile(r"(?:HTTP\s+)?(?P<status>[1-5][0-9]{2})", re.IGNORECASE)
DEFAULT_ADAPTER_ORDER = ("async", "sync", "browser")


class RequestContextRejected(RuntimeError):
    """One concrete request context was rejected and should not be retried here."""

    def __init__(self, adapter: str, *, status: int = 429) -> None:
        self.adapter = adapter
        self.status = status
        super().__init__(f"request context rejected: adapter={adapter} status={status}")


class GraphAcquisitionFailed(RuntimeError):
    """No configured adapter returned a valid exact-thread graph."""

    def __init__(
        self,
        attempts: Sequence[dict[str, Any]],
        observations: Sequence[dict[str, Any]] = (),
    ) -> None:
        self.attempts = tuple(dict(item) for item in attempts)
        self.observations = tuple(dict(item) for item in observations)
        self.attempted_adapters = tuple(str(item["adapter"]) for item in self.attempts)
        self.rejected_adapters = tuple(
            str(item["adapter"]) for item in self.attempts if item.get("rejected")
        )
        super().__init__(
            "no exact-thread graph adapter succeeded; attempted="
            + ",".join(self.attempted_adapters)
        )


@dataclass(frozen=True)
class GraphAcquisitionResult:
    graph: dict[str, Any]
    adapter: str
    elapsed_ms: float
    diagnostic: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]
    attempted_adapters: tuple[str, ...]
    rejected_adapters: tuple[str, ...]
    observations: tuple[dict[str, Any], ...] = ()


def validate_exact_graph(graph: Any) -> dict[str, Any]:
    if not isinstance(graph, dict):
        raise ValueError("conversation response is not a JSON object")
    mapping = graph.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("conversation response has no non-empty mapping")
    return graph


def _status_from_exception(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    match = _STATUS_RE.search(str(exc))
    return int(match.group("status")) if match else None


def _is_request_context_rejection(exc: BaseException) -> bool:
    if isinstance(exc, RequestContextRejected):
        return True
    status = _status_from_exception(exc)
    if status == 429:
        return True
    return "too many requests" in str(exc).lower()


def _safe_attempt(
    adapter: str,
    *,
    elapsed_ms: float,
    status: int | None,
    response_class: str,
    rejected: bool,
) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "elapsed_ms": round(float(elapsed_ms), 3),
        "status": status,
        "response_class": response_class,
        "rejected": bool(rejected),
    }


def _normalize_fetch_result(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, tuple) and len(value) == 2:
        graph, diagnostic = value
        return validate_exact_graph(graph), dict(diagnostic or {})
    return validate_exact_graph(value), {}


def _resolve_direct_context(
    session_token: str | None,
    model: str | None,
    user_agent: str | None,
) -> tuple[str | None, str | None, str]:
    token = session_token
    if token is None:
        try:
            token = get_session_token()
        except Exception:
            token = None
    return token, model or get_default_model(), user_agent or get_default_user_agent()


def _build_default_fetchers(
    *,
    session_token: str | None,
    model: str | None,
    user_agent: str | None,
    profile_dir: Path | str | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
) -> dict[str, Callable[[str], Any]]:
    token, default_model, agent = _resolve_direct_context(session_token, model, user_agent)
    snapshot = load_browser_session_snapshot(profile_dir=profile_dir, max_age_seconds=3600)

    def fetch_async(conversation_id: str):
        async def run() -> tuple[dict[str, Any], dict[str, Any]]:
            async with AsyncChatGPT(
                session_token=token,
                default_model=default_model,
                user_agent=agent,
                browser_session=snapshot,
            ) as client:
                raw_headers = dict(client.build_request_headers())
                raw_headers["Accept"] = "application/json"
                raw_headers.pop("Content-Type", None)
                graph = await client.fetch_conversation(conversation_id)
                obs = build_protocol_observation(
                    transport="async",
                    operation="exact_read",
                    method="GET",
                    path=f"/backend-api/conversation/{conversation_id}",
                    status=200,
                    response_class="ok",
                    headers=raw_headers,
                    cookies=getattr(client.session, "cookies", None),
                )
                return graph, obs.to_dict()

        return asyncio.run(run())

    def fetch_sync(conversation_id: str):
        with SyncChatGPT(
            session_token=token,
            default_model=default_model,
            user_agent=agent,
            browser_session=snapshot,
        ) as client:
            raw_headers = dict(client.build_request_headers())
            raw_headers["Accept"] = "application/json"
            raw_headers.pop("Content-Type", None)
            graph = client.fetch_conversation(conversation_id)
            obs = build_protocol_observation(
                transport="sync",
                operation="exact_read",
                method="GET",
                path=f"/backend-api/conversation/{conversation_id}",
                status=200,
                response_class="ok",
                headers=raw_headers,
                cookies=getattr(client.session, "cookies", None),
            )
            return graph, obs.to_dict()

    def fetch_browser(conversation_id: str):
        result = fetch_conversation_same_origin(
            conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            timeout=browser_timeout,
        )
        return result.chat, dict(result.diagnostic)

    return {"async": fetch_async, "sync": fetch_sync, "browser": fetch_browser}


def fetch_exact_graph(
    conversation_id: str,
    *,
    session_token: str | None = None,
    model: str | None = None,
    user_agent: str | None = None,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    adapter_order: Sequence[str] = DEFAULT_ADAPTER_ORDER,
    adapter_fetchers: Mapping[str, Callable[[str], Any]] | None = None,
) -> GraphAcquisitionResult:
    """Return the first validated exact-thread graph from the configured adapters.

    A rejected adapter is attempted once and then abandoned for this broker call.
    Failures are represented only by redacted class/status/timing metadata.
    """

    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id must be provided")

    fetchers = dict(adapter_fetchers or _build_default_fetchers(
        session_token=session_token,
        model=model,
        user_agent=user_agent,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
    ))

    attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    rejected_adapters: list[str] = []
    seen: set[str] = set()
    seq = 0

    for adapter in adapter_order:
        if adapter in seen:
            continue
        seen.add(adapter)
        fetcher = fetchers.get(adapter)
        if fetcher is None:
            continue
        seq += 1
        started = time.perf_counter()
        try:
            graph, diagnostic = _normalize_fetch_result(fetcher(conversation_id))
        except Exception as exc:  # noqa: BLE001 - broker intentionally isolates adapter failures.
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            rejected = _is_request_context_rejection(exc)
            if rejected:
                rejected_adapters.append(adapter)
            status = _status_from_exception(exc)
            attempts.append(
                _safe_attempt(
                    adapter,
                    elapsed_ms=elapsed_ms,
                    status=status,
                    response_class=exc.__class__.__name__,
                    rejected=rejected,
                )
            )
            observations.append(
                observation_from_exception(
                    exc,
                    transport=adapter,
                    operation="exact_read",
                    method="GET",
                    path=f"/backend-api/conversation/{conversation_id}",
                    sequence_index=seq,
                    elapsed_ms=elapsed_ms,
                ).to_dict()
            )
            continue

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        status = diagnostic.get("status")
        resolved_status = int(status) if isinstance(status, int) else 200
        resolved_class = str(diagnostic.get("response_class") or "ok")
        attempts.append(
            _safe_attempt(
                adapter,
                elapsed_ms=elapsed_ms,
                status=resolved_status,
                response_class=resolved_class,
                rejected=False,
            )
        )
        obs = build_protocol_observation(
            transport=adapter,
            operation="exact_read",
            method=str(diagnostic.get("method") or "GET"),
            path=str(diagnostic.get("route") or f"/backend-api/conversation/{conversation_id}"),
            status=resolved_status,
            response_class=resolved_class,
            headers={name: "<redacted>" for name in diagnostic.get("header_names") or ()},
            cookies=diagnostic.get("cookie_names") or (),
            cookie_domains=diagnostic.get("cookie_domains") or (),
            sequence_index=seq,
            elapsed_ms=elapsed_ms,
        ).to_dict()
        observations.append(obs)
        return GraphAcquisitionResult(
            graph=graph,
            adapter=adapter,
            elapsed_ms=elapsed_ms,
            diagnostic=diagnostic,
            attempts=tuple(attempts),
            attempted_adapters=tuple(item["adapter"] for item in attempts),
            rejected_adapters=tuple(rejected_adapters),
            observations=tuple(observations),
        )

    raise GraphAcquisitionFailed(attempts, observations)
