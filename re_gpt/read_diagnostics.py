"""Redacted differential diagnostics for exact-thread read adapters.

This module records request *shape* only: names/domains, status, timing and
response class. It never emits token, cookie or device-id values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .async_chatgpt import AsyncChatGPT
from .browser_observer import fetch_conversation_same_origin
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token

from .protocol_observation import (
    build_protocol_observation,
    observation_from_exception,
    status_from_exception,
)


def redacted_request_shape(
    *,
    adapter: str,
    method: str,
    route: str,
    headers: Mapping[str, Any],
    cookie_names: Iterable[str] = (),
    cookie_domains: Iterable[str] = (),
    status: int | None = None,
    elapsed_ms: float | None = None,
    response_class: str,
    operation: str = "exact_read",
    payload: Any = None,
) -> dict[str, Any]:
    obs = build_protocol_observation(
        transport=adapter,
        operation=operation,
        method=method,
        path=route,
        status=status,
        response_class=response_class,
        headers=headers,
        cookies=cookie_names,
        cookie_domains=cookie_domains,
        payload=payload,
        elapsed_ms=elapsed_ms,
    )
    return obs.to_dict()


def _status_from_error(exc: BaseException) -> int | None:
    return status_from_exception(exc)


def _cookie_metadata(session: Any) -> tuple[list[str], list[str]]:
    jar = getattr(session, "cookies", None)
    if jar is None:
        return [], []
    names: set[str] = set()
    domains: set[str] = set()
    try:
        iterable = list(jar.jar) if hasattr(jar, "jar") else list(jar)
    except Exception:
        iterable = []
    for cookie in iterable:
        name = getattr(cookie, "name", None)
        domain = getattr(cookie, "domain", None)
        if name:
            names.add(str(name))
        if domain:
            domains.add(str(domain))
    return sorted(names), sorted(domains)


def _validate_graph(chat: Any) -> None:
    if not isinstance(chat, dict):
        raise ValueError("conversation response is not a JSON object")
    mapping = chat.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("conversation response has no non-empty mapping")


def _sync_probe(conversation_id: str, *, token: str | None, model: str | None, user_agent: str) -> dict[str, Any]:
    started = time.perf_counter()
    route = "/backend-api/conversation/<id>"
    headers: Mapping[str, Any] = {}
    cookie_names: list[str] = []
    cookie_domains: list[str] = []
    try:
        with SyncChatGPT(session_token=token, default_model=model, user_agent=user_agent) as client:
            raw_headers = dict(client.build_request_headers())
            raw_headers["Accept"] = "application/json"
            raw_headers.pop("Content-Type", None)
            headers = raw_headers
            cookie_names, cookie_domains = _cookie_metadata(client.session)
            chat = client.fetch_conversation(conversation_id)
            _validate_graph(chat)
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve adapter failure class.
        return observation_from_exception(
            exc,
            transport="sync",
            operation="exact_read",
            method="GET",
            path=route,
            headers=headers,
            cookies=cookie_names,
            cookie_domains=cookie_domains,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        ).to_dict()
    return redacted_request_shape(
        adapter="sync",
        method="GET",
        route=route,
        headers=headers,
        cookie_names=cookie_names,
        cookie_domains=cookie_domains,
        status=200,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        response_class="ok",
    )


async def _async_probe(conversation_id: str, *, token: str | None, model: str | None, user_agent: str) -> dict[str, Any]:
    started = time.perf_counter()
    route = "/backend-api/conversation/<id>"
    headers: Mapping[str, Any] = {}
    cookie_names: list[str] = []
    cookie_domains: list[str] = []
    try:
        async with AsyncChatGPT(session_token=token, default_model=model, user_agent=user_agent) as client:
            raw_headers = dict(client.build_request_headers())
            raw_headers["Accept"] = "application/json"
            raw_headers.pop("Content-Type", None)
            headers = raw_headers
            cookie_names, cookie_domains = _cookie_metadata(client.session)
            chat = await client.fetch_conversation(conversation_id)
            _validate_graph(chat)
    except Exception as exc:  # noqa: BLE001
        return observation_from_exception(
            exc,
            transport="async",
            operation="exact_read",
            method="GET",
            path=route,
            headers=headers,
            cookies=cookie_names,
            cookie_domains=cookie_domains,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        ).to_dict()
    return redacted_request_shape(
        adapter="async",
        method="GET",
        route=route,
        headers=headers,
        cookie_names=cookie_names,
        cookie_domains=cookie_domains,
        status=200,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        response_class="ok",
    )


def diagnose_reads(
    conversation_id: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Probe sync, async and browser reads and return only redacted receipts."""

    try:
        token = get_session_token()
    except Exception:  # noqa: BLE001 - probe constructors will report the missing direct context.
        token = None
    model = get_default_model()
    user_agent = get_default_user_agent()
    receipts = [_sync_probe(conversation_id, token=token, model=model, user_agent=user_agent)]
    receipts.append(asyncio.run(_async_probe(conversation_id, token=token, model=model, user_agent=user_agent)))

    started = time.perf_counter()
    try:
        browser = fetch_conversation_same_origin(
            conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        receipts.append(
            observation_from_exception(
                exc,
                transport="browser",
                operation="exact_read",
                method="GET",
                path="/backend-api/conversation/<id>",
                headers={},
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            ).to_dict()
        )
    else:
        raw_diag = dict(browser.diagnostic)
        obs = build_protocol_observation(
            transport=str(raw_diag.get("adapter") or "browser"),
            operation="exact_read",
            method=str(raw_diag.get("method") or "GET"),
            path=str(raw_diag.get("route") or "/backend-api/conversation/<id>"),
            status=raw_diag.get("status"),
            response_class=str(raw_diag.get("response_class") or "ok"),
            headers={name: "<redacted>" for name in raw_diag.get("header_names") or ()},
            cookies=raw_diag.get("cookie_names") or (),
            cookie_domains=raw_diag.get("cookie_domains") or (),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        receipts.append(obs.to_dict())
    return receipts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare redacted exact-thread read request shapes")
    parser.add_argument("conversation_id")
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=60)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        receipts = diagnose_reads(
            args.conversation_id,
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            timeout=args.timeout,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}")
        return 1
    print(json.dumps(receipts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
