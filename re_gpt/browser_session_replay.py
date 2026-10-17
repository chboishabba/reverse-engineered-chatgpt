"""Record and replay authenticated browser session and fingerprint state for CFFI clients.

This module captures the owned browser profile's cookie jar, bearer access token,
account/device identifiers, User-Agent / Client-Hints headers, navigator/screen
contextual fingerprint, and pre-minted sentinel token bundle so ``AsyncChatGPT``
and ``SyncChatGPT`` can replay the accepted browser session directly over
``curl_cffi`` without driving the conversation DOM.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from chatgpt_dom_relay import DEFAULT_PROFILE_DIR

from .accessibility_browser import AccessibilityBrowserRelay, BROWSER_MODES

SNAPSHOT_FILENAME = ".re_gpt_session_snapshot.json"
DEFAULT_SNAPSHOT_PATH = Path(DEFAULT_PROFILE_DIR) / SNAPSHOT_FILENAME

_REPLAYABLE_HEADER_NAMES = {
    "accept-language",
    "chatgpt-account-id",
    "oai-client-build-number",
    "oai-client-version",
    "oai-did",
    "oai-language",
    "oai-session-id",
    "originator",
    "sec-ch-ua",
    "sec-ch-ua-arch",
    "sec-ch-ua-bitness",
    "sec-ch-ua-full-version",
    "sec-ch-ua-full-version-list",
    "sec-ch-ua-mobile",
    "sec-ch-ua-model",
    "sec-ch-ua-platform",
    "sec-ch-ua-platform-version",
    "user-agent",
    "x-openai-codex-window-type",
    "x-openai-web-frontend",
}


def resolve_snapshot_path(
    profile_dir: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> Path:
    if snapshot_path is not None:
        return Path(snapshot_path).expanduser()
    env_path = os.environ.get("RE_GPT_BROWSER_SESSION_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    base_dir = Path(profile_dir).expanduser() if profile_dir is not None else Path(DEFAULT_PROFILE_DIR).expanduser()
    return base_dir / SNAPSHOT_FILENAME


@dataclass
class BrowserSessionSnapshot:
    captured_at: float = field(default_factory=time.time)
    profile_dir: str | None = None
    access_token: str | None = None
    account_id: str | None = None
    device_id: str | None = None
    user_agent: str | None = None
    cookies: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    sentinel: dict[str, Any] = field(default_factory=dict)

    def cookie_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self.cookies:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            value = item.get("value")
            domain = str(item.get("domain") or "chatgpt.com").lstrip(".")
            if name and value is not None and domain.endswith("chatgpt.com"):
                result[name] = str(value)
        return result

    def has_fresh_sentinel(self, *, now: float | None = None) -> bool:
        if not isinstance(self.sentinel, Mapping):
            return False
        token = str(self.sentinel.get("chat_requirements_token") or "").strip()
        if not token or bool(self.sentinel.get("consumed")):
            return False
        current = time.time() if now is None else float(now)
        expire_at = self.sentinel.get("expire_at")
        if isinstance(expire_at, (int, float)) and float(expire_at) > 0:
            return current + 15.0 < float(expire_at)
        captured = float(self.sentinel.get("captured_at") or self.captured_at or 0.0)
        expire_after = float(self.sentinel.get("expire_after") or 300.0)
        return (current - captured) + 15.0 < expire_after

    def sentinel_headers(self) -> dict[str, str]:
        if not isinstance(self.sentinel, Mapping):
            return {}
        headers: dict[str, str] = {}
        req_token = str(self.sentinel.get("chat_requirements_token") or "").strip()
        proof_token = str(self.sentinel.get("proof_token") or "").strip()
        turnstile_token = str(self.sentinel.get("turnstile_token") or "").strip()
        if req_token:
            headers["openai-sentinel-chat-requirements-token"] = req_token
        if proof_token:
            headers["openai-sentinel-proof-token"] = proof_token
        if turnstile_token:
            headers["openai-sentinel-turnstile-token"] = turnstile_token
        return headers

    def build_replay_headers(self, *, conversation_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in (self.headers or {}).items():
            lower = str(key).strip().lower()
            if lower in _REPLAYABLE_HEADER_NAMES and value is not None and str(value).strip():
                headers[lower] = str(value).strip()
        if self.user_agent and "user-agent" not in headers:
            headers["user-agent"] = self.user_agent
        if self.account_id and "chatgpt-account-id" not in headers:
            headers["chatgpt-account-id"] = self.account_id
        if self.device_id and "oai-did" not in headers:
            headers["oai-did"] = self.device_id
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}" if conversation_id else "https://chatgpt.com/"
        return headers

    def client_contextual_info(self) -> dict[str, Any]:
        fp = self.fingerprint if isinstance(self.fingerprint, Mapping) else {}
        ctx = fp.get("client_contextual_info")
        if isinstance(ctx, Mapping) and ctx:
            return dict(ctx)
        return {
            "app_name": "chatgpt.com",
            "app_surface": "codex_browser",
            "has_web_push_capabilities": True,
            "web_push_notification_permission": "default",
        }

    def timezone(self) -> str:
        fp = self.fingerprint if isinstance(self.fingerprint, Mapping) else {}
        tz = str(fp.get("timezone") or "").strip()
        return tz or "UTC"

    def timezone_offset_min(self) -> int:
        fp = self.fingerprint if isinstance(self.fingerprint, Mapping) else {}
        raw = fp.get("timezone_offset_min")
        if isinstance(raw, (int, float)):
            return int(raw)
        return 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_redacted_summary(self) -> dict[str, Any]:
        cookie_names = sorted({str(c.get("name") or "") for c in self.cookies if isinstance(c, Mapping) and c.get("name")})
        header_names = sorted({str(k).lower() for k in (self.headers or {}).keys() if k})
        return {
            "captured_at": self.captured_at,
            "has_access_token": bool(self.access_token),
            "has_account_id": bool(self.account_id),
            "has_device_id": bool(self.device_id),
            "cookie_count": len(cookie_names),
            "cookie_names": cookie_names,
            "header_names": header_names,
            "has_fresh_sentinel": self.has_fresh_sentinel(),
            "has_proof_token": bool((self.sentinel or {}).get("proof_token")),
            "has_turnstile_token": bool((self.sentinel or {}).get("turnstile_token")),
            "timezone": self.timezone(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BrowserSessionSnapshot":
        return cls(
            captured_at=float(payload.get("captured_at") or time.time()),
            profile_dir=str(payload["profile_dir"]) if payload.get("profile_dir") else None,
            access_token=str(payload["access_token"]) if payload.get("access_token") else None,
            account_id=str(payload["account_id"]) if payload.get("account_id") else None,
            device_id=str(payload["device_id"]) if payload.get("device_id") else None,
            user_agent=str(payload["user_agent"]) if payload.get("user_agent") else None,
            cookies=[dict(item) for item in (payload.get("cookies") or []) if isinstance(item, Mapping)],
            headers={str(k).lower(): str(v) for k, v in (payload.get("headers") or {}).items() if v is not None},
            fingerprint=dict(payload.get("fingerprint") or {}),
            sentinel=dict(payload.get("sentinel") or {}),
        )


def save_browser_session_snapshot(
    snapshot: BrowserSessionSnapshot,
    *,
    profile_dir: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> Path:
    """Atomically save snapshot secrets in a file that is private from birth."""
    target = resolve_snapshot_path(profile_dir=profile_dir or snapshot.profile_dir, snapshot_path=snapshot_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def load_browser_session_snapshot(
    *,
    profile_dir: Path | str | None = None,
    snapshot_path: Path | str | None = None,
    max_age_seconds: float | None = None,
) -> BrowserSessionSnapshot | None:
    target = resolve_snapshot_path(profile_dir=profile_dir, snapshot_path=snapshot_path)
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return None
        snapshot = BrowserSessionSnapshot.from_dict(raw)
    except Exception:
        return None
    if max_age_seconds is not None and (time.time() - snapshot.captured_at) > float(max_age_seconds):
        return None
    return snapshot


def mark_sentinel_consumed(
    snapshot: BrowserSessionSnapshot | None = None,
    *,
    profile_dir: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> None:
    loaded = snapshot or load_browser_session_snapshot(profile_dir=profile_dir, snapshot_path=snapshot_path)
    if loaded is None:
        return
    sentinel = dict(loaded.sentinel or {})
    sentinel["consumed"] = True
    loaded.sentinel = sentinel
    try:
        save_browser_session_snapshot(loaded, profile_dir=profile_dir, snapshot_path=snapshot_path)
    except Exception:
        pass


def filter_replayable_headers(headers: Mapping[str, Any] | None) -> tuple[dict[str, str], str | None]:
    """Extract replayable fingerprint headers and bearer token from raw request headers."""
    replay_headers: dict[str, str] = {}
    access_token: str | None = None
    for raw_key, raw_val in (headers or {}).items():
        if raw_val is None:
            continue
        key = str(raw_key).strip().lower()
        val = str(raw_val).strip()
        if not key or not val:
            continue
        if key == "authorization" and val.lower().startswith("bearer "):
            token_candidate = val[7:].strip()
            if token_candidate:
                access_token = token_candidate
            continue
        if key in _REPLAYABLE_HEADER_NAMES:
            replay_headers[key] = val
    return replay_headers, access_token


def apply_browser_session_to_client(client: Any, snapshot: BrowserSessionSnapshot) -> None:
    """Hydrate an AsyncChatGPT or SyncChatGPT instance with a recorded browser session."""
    if not isinstance(snapshot, BrowserSessionSnapshot):
        return
    cookie_map = snapshot.cookie_map()
    session_token = cookie_map.get("__Secure-next-auth.session-token") or cookie_map.get("__Secure-next-auth.session-token.0")
    if session_token and not getattr(client, "session_token", None):
        client.session_token = session_token
        client.free_mode = False
    if snapshot.access_token:
        client.auth_token = snapshot.access_token
        client.free_mode = False
    if snapshot.device_id:
        client.devive_id = snapshot.device_id
    if snapshot.user_agent:
        client.user_agent = snapshot.user_agent
    if snapshot.fingerprint:
        tz = snapshot.timezone()
        if tz:
            client.timezone = tz
        client.timezone_offset_min = snapshot.timezone_offset_min()
        client.client_contextual_info = snapshot.client_contextual_info()
    frontend_cookies = getattr(client, "_frontend_cookies", None)
    if isinstance(frontend_cookies, dict):
        frontend_cookies.update(cookie_map)
    session = getattr(client, "session", None)
    if session is not None and hasattr(session, "cookies"):
        for item in snapshot.cookies:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            value = item.get("value")
            domain = str(item.get("domain") or "chatgpt.com").lstrip(".") or "chatgpt.com"
            path = str(item.get("path") or "/") or "/"
            if not name or value is None or not domain.endswith("chatgpt.com"):
                continue
            try:
                session.cookies.set(name, str(value), domain=domain, path=path)
            except Exception:
                try:
                    session.cookies.set(name, str(value))
                except Exception:
                    pass
    client._replayed_browser_snapshot = snapshot


async def capture_browser_session_snapshot_async(
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 30,
    idle_ms: int = 2500,
    save: bool = True,
    snapshot_path: Path | str | None = None,
) -> BrowserSessionSnapshot:
    """Open the lightweight ChatGPT root context and capture session/fingerprint/sentinel state."""
    resolved_profile = Path(profile_dir or DEFAULT_PROFILE_DIR).expanduser()
    captured_headers: dict[str, str] = {}
    captured_access_token: str | None = None
    finalize_req: dict[str, Any] = {}
    finalize_resp: dict[str, Any] = {}

    async with AccessibilityBrowserRelay(
        profile_dir=resolved_profile,
        browser_mode=browser_mode if browser_mode in BROWSER_MODES else "displayless",
        cdp_endpoint=cdp_endpoint,
        timeout_ms=max(5, int(timeout)) * 1000,
        idle_ms=idle_ms,
    ) as relay:
        async def on_response(resp: Any) -> None:
            nonlocal captured_access_token
            url = str(getattr(resp, "url", "") or "")
            if "/backend-api/" not in url:
                return
            req = getattr(resp, "request", None)
            raw_headers = getattr(req, "headers", None) if req is not None else None
            if callable(raw_headers):
                raw_headers = raw_headers()
            if isinstance(raw_headers, Mapping):
                filtered, bearer = filter_replayable_headers(raw_headers)
                if filtered:
                    captured_headers.update(filtered)
                if bearer:
                    captured_access_token = bearer
            if url.endswith("/sentinel/chat-requirements/finalize") and str(getattr(req, "method", "")).upper() == "POST":
                try:
                    post_data = req.post_data() if callable(getattr(req, "post_data", None)) else getattr(req, "post_data", None)
                    if isinstance(post_data, str) and post_data:
                        parsed_req = json.loads(post_data)
                        if isinstance(parsed_req, Mapping):
                            finalize_req.update(parsed_req)
                    parsed_resp = await resp.json()
                    if isinstance(parsed_resp, Mapping):
                        finalize_resp.update(parsed_resp)
                except Exception:
                    pass

        relay.page.on("response", on_response)
        await relay.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=max(5, int(timeout)) * 1000)
        for _ in range(25):
            if finalize_resp and captured_headers:
                break
            await relay.page.wait_for_timeout(250)

        env_info = await relay.page.evaluate(
            """async () => {
                let session = {};
                try {
                    const resp = await fetch("/api/auth/session", {method: "GET"});
                    if (resp.ok) session = await resp.json();
                } catch (_) {}
                let uaData = null;
                try {
                    if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
                        uaData = await navigator.userAgentData.getHighEntropyValues([
                            "architecture", "bitness", "fullVersionList", "model", "platformVersion", "uaFullVersion"
                        ]);
                    }
                } catch (_) {}
                return {
                    accessToken: session.accessToken || null,
                    accountId: (session.account && session.account.id) || null,
                    userAgent: navigator.userAgent || null,
                    language: navigator.language || "en-US",
                    languages: Array.isArray(navigator.languages) ? navigator.languages : ["en-US"],
                    platform: navigator.platform || "",
                    hardwareConcurrency: navigator.hardwareConcurrency || null,
                    deviceMemory: navigator.deviceMemory || null,
                    uaData,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
                    timezone_offset_min: new Date().getTimezoneOffset(),
                    innerWidth: window.innerWidth || 1440,
                    innerHeight: window.innerHeight || 900,
                    devicePixelRatio: window.devicePixelRatio || 1,
                    isDarkMode: Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches),
                };
            }"""
        )
        cookies = await relay.page.context.cookies("https://chatgpt.com")

    cookie_list = [dict(c) for c in (cookies or []) if isinstance(c, Mapping)]
    cookie_lookup = {str(c.get("name") or ""): str(c.get("value") or "") for c in cookie_list}
    access_token = str(env_info.get("accessToken") or captured_access_token or "").strip() or None
    account_id = str(captured_headers.get("chatgpt-account-id") or env_info.get("accountId") or cookie_lookup.get("_account") or "").strip() or None
    device_id = str(captured_headers.get("oai-did") or cookie_lookup.get("oai-did") or "").strip() or None
    user_agent = str(captured_headers.get("user-agent") or env_info.get("userAgent") or "").strip() or None

    fingerprint = {
        "userAgent": user_agent,
        "language": env_info.get("language"),
        "languages": env_info.get("languages"),
        "platform": env_info.get("platform"),
        "hardwareConcurrency": env_info.get("hardwareConcurrency"),
        "deviceMemory": env_info.get("deviceMemory"),
        "uaData": env_info.get("uaData"),
        "timezone": env_info.get("timezone") or "UTC",
        "timezone_offset_min": int(env_info.get("timezone_offset_min") or 0),
        "innerWidth": env_info.get("innerWidth") or 1440,
        "innerHeight": env_info.get("innerHeight") or 900,
        "devicePixelRatio": env_info.get("devicePixelRatio") or 1,
        "isDarkMode": bool(env_info.get("isDarkMode")),
        "client_contextual_info": {
            "app_name": "chatgpt.com",
            "app_surface": "codex_browser",
            "has_web_push_capabilities": True,
            "web_push_notification_permission": "default",
        },
    }

    sentinel: dict[str, Any] = {}
    if finalize_resp.get("token"):
        sentinel = {
            "chat_requirements_token": str(finalize_resp["token"]),
            "proof_token": str(finalize_req.get("proofofwork") or ""),
            "turnstile_token": str(finalize_req.get("turnstile") or ""),
            "prepare_token": str(finalize_req.get("prepare_token") or ""),
            "expire_after": finalize_resp.get("expire_after"),
            "expire_at": finalize_resp.get("expire_at"),
            "persona": finalize_resp.get("persona"),
            "captured_at": time.time(),
            "consumed": False,
        }

    snapshot = BrowserSessionSnapshot(
        captured_at=time.time(),
        profile_dir=str(resolved_profile),
        access_token=access_token,
        account_id=account_id,
        device_id=device_id,
        user_agent=user_agent,
        cookies=cookie_list,
        headers=captured_headers,
        fingerprint=fingerprint,
        sentinel=sentinel,
    )
    if save:
        save_browser_session_snapshot(snapshot, profile_dir=resolved_profile, snapshot_path=snapshot_path)
    return snapshot


def capture_browser_session_snapshot(
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 30,
    idle_ms: int = 2500,
    save: bool = True,
    snapshot_path: Path | str | None = None,
) -> BrowserSessionSnapshot:
    return asyncio.run(
        capture_browser_session_snapshot_async(
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            timeout=timeout,
            idle_ms=idle_ms,
            save=save,
            snapshot_path=snapshot_path,
        )
    )
