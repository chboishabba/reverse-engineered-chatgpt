"""Redaction-first browser/CDP protocol recorder for ChatGPT same-origin traffic.

Captures ordered ProtocolObservation records from Playwright request/response
events around a bounded browser action without packet sniffing, TLS MITM, or
persisting secret values/raw bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from chatgpt_dom_relay import DEFAULT_PROFILE_DIR, extract_thread_id

from .accessibility_browser import AccessibilityBrowserRelay, BROWSER_MODES
from .browser_observer_runner import _dom_status, _same_origin_read
from .browser_session_replay import (
    BrowserSessionSnapshot,
    filter_replayable_headers,
    load_browser_session_snapshot,
    save_browser_session_snapshot,
)
from .protocol_observation import (
    ProtocolObservation,
    build_protocol_observation,
    normalize_protocol_path,
)

_CHATGPT_HOSTS = frozenset({"chatgpt.com", "www.chatgpt.com"})
_PROTOCOL_PATH_PREFIXES = ("/api/", "/backend-api/", "/backend-anon/")


def is_chatgpt_protocol_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host and host not in _CHATGPT_HOSTS:
        return False
    return any(path.startswith(prefix) for prefix in _PROTOCOL_PATH_PREFIXES)


def _infer_bootstrap_stage(path: str, method: str) -> str | None:
    norm = normalize_protocol_path(path).lower()
    if "/api/auth/session" in norm:
        return "auth_session"
    if "sentinel/chat-requirements/finalize" in norm or "sentinel/finalize" in norm:
        return "sentinel_finalize"
    if "sentinel/chat-requirements" in norm:
        return "sentinel_prepare" if "prepare" in norm else "sentinel_requirements"
    if norm.endswith("/conversation/init"):
        return "conversation_init"
    if norm.endswith("/conversation/prepare"):
        return "conversation_prepare"
    if "files" in norm or "upload" in norm or "attachments" in norm:
        return "attachment_staging"
    if norm in {"/backend-api/conversation", "/backend-api/f/conversation", "/backend-anon/conversation"} and method.upper() == "POST":
        return "conversation_post"
    if (norm.startswith("/backend-api/conversation/") or norm.startswith("/backend-api/f/conversation/")) and method.upper() == "GET":
        return "conversation_get"
    return None


def _classify_response(status: int | None, response_headers: Mapping[str, Any] | None) -> str:
    if status is None:
        return "no_response"
    content_type = ""
    if response_headers:
        for k, v in response_headers.items():
            if str(k).lower() == "content-type":
                content_type = str(v).lower()
                break
    if status == 429:
        return "rejected_429"
    if status >= 400:
        return f"http_{status}"
    if "text/event-stream" in content_type:
        return "sse_stream"
    if "application/json" in content_type:
        return "json_ok"
    return "ok"


class BrowserProtocolRecorder:
    """Attach to a Playwright page and collect bounded redacted observations.

    Session snapshot persistence is explicit. Passing a profile directory only
    identifies an optional snapshot source/destination; it never enables writes.
    """

    def __init__(
        self,
        page: Any,
        *,
        operation: str = "browser_action",
        cookies: Iterable[Any] | Mapping[str, Any] | None = None,
        profile_dir: Path | str | None = None,
        snapshot_path: Path | str | None = None,
        save_session_snapshot: bool = False,
    ) -> None:
        self.page = page
        self.operation = operation
        self._static_cookies = cookies
        self.profile_dir = Path(profile_dir).expanduser() if profile_dir is not None else None
        self.snapshot_path = Path(snapshot_path).expanduser() if snapshot_path is not None else None
        self.save_session_snapshot = bool(save_session_snapshot)
        self._records: list[ProtocolObservation] = []
        self._pending_starts: dict[int, float] = {}
        self._pending_requests: dict[int, Any] = {}
        self._captured_headers: dict[str, str] = {}
        self._captured_access_token: str | None = None
        self._captured_sentinel: dict[str, Any] = {}
        self._capture_tasks: list[Any] = []
        self._sequence = 0
        self._active = False

    @property
    def observations(self) -> tuple[ProtocolObservation, ...]:
        return tuple(self._records)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._records]

    async def _capture_finalize_response(self, response: Any) -> None:
        try:
            json_fn = getattr(response, "json", None)
            if callable(json_fn):
                res = json_fn()
                parsed = await res if asyncio.iscoroutine(res) else res
                if isinstance(parsed, Mapping) and parsed.get("token"):
                    self._captured_sentinel["chat_requirements_token"] = str(parsed["token"])
                    self._captured_sentinel["expire_after"] = parsed.get("expire_after")
                    self._captured_sentinel["expire_at"] = parsed.get("expire_at")
                    self._captured_sentinel["persona"] = parsed.get("persona")
                    self._captured_sentinel["captured_at"] = time.time()
                    self._captured_sentinel["consumed"] = False
        except Exception:
            pass

    def _capture_request_artifacts(self, url: str, method: str, req_headers: Any, post_data: Any) -> None:
        if isinstance(req_headers, Mapping):
            filtered, bearer = filter_replayable_headers(req_headers)
            if filtered:
                self._captured_headers.update(filtered)
            if bearer:
                self._captured_access_token = bearer
            lower_headers = {str(k).lower(): str(v) for k, v in req_headers.items() if v is not None}
            if _infer_bootstrap_stage(url, method) == "conversation_post":
                req_tok = lower_headers.get("openai-sentinel-chat-requirements-token")
                if req_tok and self._captured_sentinel.get("chat_requirements_token") == req_tok:
                    self._captured_sentinel["consumed"] = True
        if method == "POST" and url.endswith("/sentinel/chat-requirements/finalize") and isinstance(post_data, str) and post_data:
            try:
                parsed = json.loads(post_data)
                if isinstance(parsed, Mapping):
                    self._captured_sentinel = {
                        "prepare_token": str(parsed.get("prepare_token") or ""),
                        "proof_token": str(parsed.get("proofofwork") or ""),
                        "turnstile_token": str(parsed.get("turnstile") or ""),
                        "captured_at": time.time(),
                        "consumed": False,
                    }
            except Exception:
                pass

    def _on_request(self, request: Any) -> None:
        if not self._active:
            return
        url = getattr(request, "url", "") or ""
        if not is_chatgpt_protocol_url(url):
            return
        self._pending_starts[id(request)] = time.perf_counter()
        self._pending_requests[id(request)] = request

    def _on_response(self, response: Any) -> None:
        if not self._active:
            return
        request = getattr(response, "request", None)
        url = (getattr(response, "url", None) or getattr(request, "url", "") or "")
        if not is_chatgpt_protocol_url(url):
            return
        started = self._pending_starts.pop(id(request), None) if request is not None else None
        if request is not None:
            self._pending_requests.pop(id(request), None)
        elapsed_ms = (time.perf_counter() - started) * 1000.0 if started is not None else None
        self.record_exchange(request=request, response=response, elapsed_ms=elapsed_ms)
        if url.endswith("/sentinel/chat-requirements/finalize"):
            try:
                loop = asyncio.get_running_loop()
                self._capture_tasks.append(loop.create_task(self._capture_finalize_response(response)))
            except RuntimeError:
                pass

    def _on_request_failed(self, request: Any) -> None:
        if not self._active:
            return
        url = getattr(request, "url", "") or ""
        if not is_chatgpt_protocol_url(url):
            return
        started = self._pending_starts.pop(id(request), None)
        self._pending_requests.pop(id(request), None)
        elapsed_ms = (time.perf_counter() - started) * 1000.0 if started is not None else None
        self.record_exchange(request=request, response=None, elapsed_ms=elapsed_ms, response_class="request_failed")

    def record_exchange(
        self,
        *,
        request: Any,
        response: Any | None = None,
        cookies: Iterable[Any] | Mapping[str, Any] | None = None,
        elapsed_ms: float | None = None,
        response_class: str | None = None,
    ) -> ProtocolObservation | None:
        url = getattr(request, "url", None) or (getattr(response, "url", None) if response is not None else "") or ""
        if not is_chatgpt_protocol_url(url):
            return None
        method = str(getattr(request, "method", "GET") or "GET").upper()
        req_headers = getattr(request, "headers", None)
        if callable(req_headers):
            req_headers = req_headers()
        post_data = getattr(request, "post_data", None)
        if callable(post_data):
            try:
                post_data = post_data()
            except Exception:
                post_data = None
        self._capture_request_artifacts(url, method, req_headers, post_data)

        status: int | None = None
        resp_headers: Mapping[str, Any] | None = None
        if response is not None:
            raw_status = getattr(response, "status", None)
            status = int(raw_status() if callable(raw_status) else raw_status) if raw_status is not None else None
            raw_resp_headers = getattr(response, "headers", None)
            resp_headers = raw_resp_headers() if callable(raw_resp_headers) else raw_resp_headers

        self._sequence += 1
        effective_cookies = cookies if cookies is not None else self._static_cookies
        obs = build_protocol_observation(
            transport="browser",
            operation=self.operation,
            method=method,
            path=url,
            status=status,
            response_class=response_class or _classify_response(status, resp_headers),
            headers=req_headers if isinstance(req_headers, Mapping) else {},
            cookies=effective_cookies,
            payload=post_data,
            sequence_index=self._sequence,
            elapsed_ms=elapsed_ms,
            bootstrap_stage=_infer_bootstrap_stage(url, method),
        )
        self._records.append(obs)
        return obs

    def start(self) -> "BrowserProtocolRecorder":
        if self._active:
            return self
        self._active = True
        if hasattr(self.page, "on"):
            self.page.on("request", self._on_request)
            self.page.on("response", self._on_response)
            self.page.on("requestfailed", self._on_request_failed)
        return self

    def stop(self) -> tuple[ProtocolObservation, ...]:
        if not self._active:
            return self.observations
        self._active = False
        if hasattr(self.page, "remove_listener"):
            for event, handler in (("request", self._on_request), ("response", self._on_response), ("requestfailed", self._on_request_failed)):
                try:
                    self.page.remove_listener(event, handler)
                except Exception:
                    pass
        for req_id, pending_req in list(self._pending_requests.items()):
            started = self._pending_starts.pop(req_id, None)
            elapsed_ms = (time.perf_counter() - started) * 1000.0 if started is not None else None
            self.record_exchange(request=pending_req, response=None, elapsed_ms=elapsed_ms, response_class="in_flight")
        self._pending_requests.clear()
        return self.observations

    def build_session_snapshot(self) -> BrowserSessionSnapshot | None:
        prior = load_browser_session_snapshot(profile_dir=self.profile_dir, snapshot_path=self.snapshot_path)
        cookie_list: list[dict[str, Any]] = []
        if isinstance(self._static_cookies, Iterable) and not isinstance(self._static_cookies, (str, bytes, Mapping)):
            cookie_list = [dict(c) for c in self._static_cookies if isinstance(c, Mapping)]
        elif prior is not None:
            cookie_list = list(prior.cookies)
        cookie_lookup = {str(c.get("name") or ""): str(c.get("value") or "") for c in cookie_list}

        headers = dict(prior.headers if prior else {})
        headers.update(self._captured_headers)
        access_token = self._captured_access_token or (prior.access_token if prior else None)
        account_id = headers.get("chatgpt-account-id") or cookie_lookup.get("_account") or (prior.account_id if prior else None)
        device_id = headers.get("oai-did") or cookie_lookup.get("oai-did") or (prior.device_id if prior else None)
        user_agent = headers.get("user-agent") or (prior.user_agent if prior else None)
        sentinel = dict(prior.sentinel if prior else {})
        if self._captured_sentinel.get("chat_requirements_token"):
            sentinel = dict(self._captured_sentinel)
        if not (access_token or cookie_list or headers):
            return None
        return BrowserSessionSnapshot(
            captured_at=time.time(),
            profile_dir=str(self.profile_dir) if self.profile_dir else (prior.profile_dir if prior else None),
            access_token=access_token,
            account_id=account_id,
            device_id=device_id,
            user_agent=user_agent,
            cookies=cookie_list,
            headers=headers,
            fingerprint=dict(prior.fingerprint if prior else {}),
            sentinel=sentinel,
        )

    async def __aenter__(self) -> "BrowserProtocolRecorder":
        if self._static_cookies is None:
            context = getattr(self.page, "context", None)
            if context is not None and hasattr(context, "cookies"):
                try:
                    self._static_cookies = await context.cookies("https://chatgpt.com")
                except Exception:
                    self._static_cookies = []
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Detach listeners before the first await so no new capture task can race
        # with the drain below.
        self.stop()
        if self._capture_tasks:
            await asyncio.gather(*tuple(self._capture_tasks), return_exceptions=True)
            self._capture_tasks.clear()
        context = getattr(self.page, "context", None)
        if context is not None and hasattr(context, "cookies"):
            try:
                self._static_cookies = await context.cookies("https://chatgpt.com")
            except Exception:
                pass
        if self.save_session_snapshot:
            snap = self.build_session_snapshot()
            if snap is not None:
                try:
                    save_browser_session_snapshot(snap, profile_dir=self.profile_dir, snapshot_path=self.snapshot_path)
                except Exception:
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record redacted ChatGPT same-origin protocol observations during one bounded browser action")
    parser.add_argument("conversation_id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read", action="store_true", help="Record same-origin exact read")
    mode.add_argument("--status", action="store_true", help="Record live DOM status load")
    mode.add_argument("--session", action="store_true", help="Capture and cache replayable browser session snapshot")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--idle-ms", type=int, default=2500)
    return parser


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    conversation_id = extract_thread_id(args.conversation_id)
    if not conversation_id:
        raise ValueError("conversation_id must contain a valid UUID")
    if getattr(args, "session", False):
        from .browser_session_replay import capture_browser_session_snapshot_async
        snap = await capture_browser_session_snapshot_async(
            profile_dir=args.profile_dir,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            timeout=args.timeout,
            idle_ms=args.idle_ms,
            save=True,
        )
        return {"conversation_id": conversation_id, "operation": "session_capture", "session_snapshot": snap.to_redacted_summary()}
    operation = "exact_read" if args.read else "dom_status"
    async with AccessibilityBrowserRelay(
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        timeout_ms=max(1, args.timeout) * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        async with BrowserProtocolRecorder(relay.page, operation=operation) as recorder:
            if args.read:
                action_result = await _same_origin_read(relay, conversation_id)
                summary = {"diagnostic": action_result.get("diagnostic", {})}
            else:
                action_result = await _dom_status(relay, conversation_id)
                summary = {"observation": action_result.get("observation", {})}
        return {"conversation_id": conversation_id, "operation": operation, "observations": recorder.to_dicts(), **summary}


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = asyncio.run(_run_cli(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
