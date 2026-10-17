"""Install global pacing around backend-api requests without changing mutation retry semantics."""

from __future__ import annotations

import asyncio
import functools
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from .global_rate_limit import GlobalBackendRateLimiter
from .protocol_observation import status_from_exception
from .stream_runtime import current_stream_state

_CONVERSATION_READ_RE = re.compile(r"^/backend-api/(?:f/)?conversation/[^/]+$")
_CONVERSATION_POST_PATHS = {
    "/backend-api/conversation",
    "/backend-api/f/conversation",
}
_INSTALLED = False


def _path(url: Any) -> str:
    raw = str(url or "")
    try:
        return urlsplit(raw).path or raw
    except Exception:
        return raw


def _is_backend(url: Any) -> bool:
    return _path(url).startswith("/backend-api/")


def _kind(method: str, url: Any) -> str:
    if method.upper() == "GET" and _CONVERSATION_READ_RE.match(_path(url)):
        return "exact_read"
    return "backend"


def _retry_after(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _report_response(limiter: GlobalBackendRateLimiter, response: Any) -> None:
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status == 429:
        limiter.report_429(retry_after=_retry_after(response))
    elif 200 <= status < 400:
        limiter.report_success(status=status)


def _report_exception(limiter: GlobalBackendRateLimiter, exc: BaseException) -> None:
    if status_from_exception(exc) == 429:
        limiter.report_429()


class PacedAsyncSession:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._re_gpt_paced = True

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"} or not callable(target):
            return target

        async def invoke(*args: Any, **kwargs: Any) -> Any:
            url = kwargs.get("url") if "url" in kwargs else (args[0] if args else "")
            method = name.upper()
            backend = _is_backend(url)
            limiter = GlobalBackendRateLimiter() if backend else None
            if limiter is not None:
                await asyncio.to_thread(limiter.acquire, kind=_kind(method, url))

            state = current_stream_state()
            if (
                state is not None
                and method == "POST"
                and _path(url) in _CONVERSATION_POST_PATHS
            ):
                original_callback = kwargs.get("content_callback")

                def stream_callback(chunk: bytes | str) -> None:
                    if original_callback is not None:
                        original_callback(chunk)
                    state.feed(chunk)

                kwargs["content_callback"] = stream_callback

            try:
                response = await target(*args, **kwargs)
            except Exception as exc:
                if limiter is not None:
                    await asyncio.to_thread(_report_exception, limiter, exc)
                raise
            if limiter is not None:
                await asyncio.to_thread(_report_response, limiter, response)
            return response

        return invoke


class PacedSyncSession:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._re_gpt_paced = True

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"} or not callable(target):
            return target

        def invoke(*args: Any, **kwargs: Any) -> Any:
            url = kwargs.get("url") if "url" in kwargs else (args[0] if args else "")
            method = name.upper()
            backend = _is_backend(url)
            limiter = GlobalBackendRateLimiter() if backend else None
            if limiter is not None:
                limiter.acquire(kind=_kind(method, url))
            try:
                response = target(*args, **kwargs)
            except Exception as exc:
                if limiter is not None:
                    _report_exception(limiter, exc)
                raise
            if limiter is not None:
                _report_response(limiter, response)
            return response

        return invoke


def _install_client_wrappers() -> None:
    from .async_chatgpt import AsyncChatGPT
    from .sync_chatgpt import SyncChatGPT

    if not getattr(AsyncChatGPT.__aenter__, "_re_gpt_global_paced", False):
        original_async_enter = AsyncChatGPT.__aenter__

        @functools.wraps(original_async_enter)
        async def paced_async_enter(self, *args: Any, **kwargs: Any):
            result = await original_async_enter(self, *args, **kwargs)
            session = getattr(self, "session", None)
            if session is not None and not getattr(session, "_re_gpt_paced", False):
                self.session = PacedAsyncSession(session)
            return result

        paced_async_enter._re_gpt_global_paced = True
        AsyncChatGPT.__aenter__ = paced_async_enter

    if not getattr(SyncChatGPT.__enter__, "_re_gpt_global_paced", False):
        original_sync_enter = SyncChatGPT.__enter__

        @functools.wraps(original_sync_enter)
        def paced_sync_enter(self, *args: Any, **kwargs: Any):
            result = original_sync_enter(self, *args, **kwargs)
            session = getattr(self, "session", None)
            if session is not None and not getattr(session, "_re_gpt_paced", False):
                self.session = PacedSyncSession(session)
            return result

        paced_sync_enter._re_gpt_global_paced = True
        SyncChatGPT.__enter__ = paced_sync_enter


def _install_browser_read_gate() -> None:
    from . import graph_broker

    original = graph_broker.fetch_conversation_same_origin
    if getattr(original, "_re_gpt_global_paced", False):
        return

    @functools.wraps(original)
    def paced_browser_read(*args: Any, **kwargs: Any):
        limiter = GlobalBackendRateLimiter()
        limiter.acquire(kind="exact_read")
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            _report_exception(limiter, exc)
            raise
        diagnostic = getattr(result, "diagnostic", None)
        status = diagnostic.get("status") if isinstance(diagnostic, dict) else None
        if status == 429:
            limiter.report_429()
        elif status is None or (isinstance(status, int) and 200 <= status < 400):
            limiter.report_success(status=int(status or 200))
        return result

    paced_browser_read._re_gpt_global_paced = True
    graph_broker.fetch_conversation_same_origin = paced_browser_read


def install_global_backend_pacing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_client_wrappers()
    _install_browser_read_gate()
    _INSTALLED = True
