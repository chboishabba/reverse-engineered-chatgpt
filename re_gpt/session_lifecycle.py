"""First-class ChatGPT web session lifecycle with redaction-first status reporting."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .async_bridge import run_async_sync
from .browser_session_replay import (
    BrowserSessionSnapshot,
    capture_browser_session_snapshot_async,
    load_browser_session_snapshot,
)

DEFAULT_EXPIRY_SKEW_SECONDS = 30.0


class StaleSessionError(ValueError):
    """Raised before mutation dispatch when persisted session expiry is provably stale."""


def _decode_jwt_payload(token: str | None) -> Mapping[str, Any] | None:
    raw = str(token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        obj = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, Mapping) else None


def token_expiry_epoch(token: str | None) -> float | None:
    payload = _decode_jwt_payload(token)
    if not payload:
        return None
    raw = payload.get("exp")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _cookie_expiry(snapshot: BrowserSessionSnapshot, name: str) -> float | None:
    values: list[float] = []
    for item in snapshot.cookies:
        if not isinstance(item, Mapping) or str(item.get("name") or "") != name:
            continue
        raw = item.get("expires", item.get("expiry"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return max(values) if values else None


def _expiry_state(expiry: float | None, *, now: float, skew: float) -> dict[str, Any]:
    if expiry is None:
        return {"state": "unknown_expiry", "expires_at": None, "seconds_remaining": None}
    remaining = float(expiry) - float(now)
    return {
        "state": "fresh" if remaining > skew else "stale",
        "expires_at": float(expiry),
        "seconds_remaining": round(remaining, 3),
    }


@dataclass
class SessionLifecycle:
    snapshot_loader: Callable[..., BrowserSessionSnapshot | None] = load_browser_session_snapshot
    capture: Callable[..., Any] = capture_browser_session_snapshot_async
    now: Callable[[], float] = time.time
    expiry_skew_seconds: float = DEFAULT_EXPIRY_SKEW_SECONDS

    def status_from_snapshot(
        self,
        snapshot: BrowserSessionSnapshot | None,
    ) -> dict[str, Any]:
        current = float(self.now())
        if snapshot is None:
            return {
                "state": "missing",
                "captured_at": None,
                "age_seconds": None,
                "access_token": {"present": False, "state": "missing", "expires_at": None, "seconds_remaining": None},
                "session_cookie": {"present": False, "state": "missing", "expires_at": None, "seconds_remaining": None},
                "sentinel": {"present": False, "state": "missing"},
                "cookie_names": [],
                "header_names": [],
            }

        access = _expiry_state(token_expiry_epoch(snapshot.access_token), now=current, skew=self.expiry_skew_seconds)
        access["present"] = bool(snapshot.access_token)
        if not snapshot.access_token:
            access["state"] = "missing"

        cookie_names = sorted({str(item.get("name") or "") for item in snapshot.cookies if isinstance(item, Mapping) and item.get("name")})
        session_names = ("__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0")
        session_present = any(name in cookie_names for name in session_names)
        cookie_expiry = None
        for name in session_names:
            expiry = _cookie_expiry(snapshot, name)
            if expiry is not None:
                cookie_expiry = max(cookie_expiry or expiry, expiry)
        cookie = _expiry_state(cookie_expiry, now=current, skew=self.expiry_skew_seconds)
        cookie["present"] = session_present
        if not session_present:
            cookie["state"] = "missing"

        sentinel_present = bool((snapshot.sentinel or {}).get("chat_requirements_token"))
        sentinel_state = "fresh" if snapshot.has_fresh_sentinel(now=current) else ("stale" if sentinel_present else "missing")

        provably_stale = access["state"] == "stale" or cookie["state"] == "stale"
        usable_auth = bool(snapshot.access_token or session_present)
        state = "stale" if provably_stale else ("ready" if usable_auth else "partial")
        return {
            "state": state,
            "captured_at": float(snapshot.captured_at),
            "age_seconds": round(max(0.0, current - float(snapshot.captured_at)), 3),
            "access_token": access,
            "session_cookie": cookie,
            "sentinel": {"present": sentinel_present, "state": sentinel_state},
            "has_account_id": bool(snapshot.account_id),
            "has_device_id": bool(snapshot.device_id),
            "cookie_names": cookie_names,
            "header_names": sorted(str(k).lower() for k in (snapshot.headers or {}).keys()),
            "timezone": snapshot.timezone(),
        }

    def status(
        self,
        *,
        profile_dir: Path | str | None = None,
        snapshot_path: Path | str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot_loader(profile_dir=profile_dir, snapshot_path=snapshot_path)
        return self.status_from_snapshot(snapshot)

    def assert_precommit_ready(
        self,
        *,
        profile_dir: Path | str | None = None,
        snapshot_path: Path | str | None = None,
        auto_refresh: bool = True,
    ) -> dict[str, Any]:
        status = self.status(profile_dir=profile_dir, snapshot_path=snapshot_path)
        if status["state"] == "stale":
            if auto_refresh:
                try:
                    refreshed = self.refresh(
                        profile_dir=profile_dir,
                        snapshot_path=snapshot_path,
                        browser_mode="displayless",
                    )
                    if refreshed.get("state") not in {"stale", "missing"}:
                        return refreshed
                except Exception:
                    pass
            raise StaleSessionError(
                "saved ChatGPT session is provably stale before mutation dispatch; refresh/bootstrap session state before retrying"
            )
        return status

    def bootstrap(
        self,
        *,
        profile_dir: Path | str | None = None,
        snapshot_path: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        timeout: int = 30,
        save: bool = True,
    ) -> dict[str, Any]:
        snapshot = run_async_sync(lambda: self.capture(
            profile_dir=profile_dir,
            snapshot_path=snapshot_path,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            timeout=timeout,
            save=save,
        ))
        if not save and snapshot is not None:
            return self.status_from_snapshot(snapshot)
        return self.status(profile_dir=profile_dir, snapshot_path=snapshot_path)

    def refresh(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["save"] = True
        return self.bootstrap(**kwargs)

    def doctor(
        self,
        *,
        profile_dir: Path | str | None = None,
        snapshot_path: Path | str | None = None,
    ) -> dict[str, Any]:
        status = self.status(profile_dir=profile_dir, snapshot_path=snapshot_path)
        actions: list[str] = []
        if status["state"] == "missing":
            diagnosis = "missing_session_snapshot"
            actions.append("bootstrap_session")
        elif status["state"] == "stale":
            diagnosis = "stale_session"
            actions.append("refresh_session")
        elif status["state"] == "partial":
            diagnosis = "partial_session"
            actions.append("bootstrap_session")
        elif status["sentinel"]["state"] != "fresh":
            diagnosis = "ready_without_fresh_sentinel"
            actions.append("refresh_before_low-latency_replay")
        else:
            diagnosis = "ready"
        return {
            "session_state": status["state"],
            "diagnosis": diagnosis,
            "recommended_actions": actions,
            "status": status,
        }
