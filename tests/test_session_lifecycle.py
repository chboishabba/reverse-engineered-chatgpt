from __future__ import annotations

import base64
import json
import time
from unittest.mock import patch

import pytest

from re_gpt.browser_session_replay import BrowserSessionSnapshot
from re_gpt.session_lifecycle import SessionLifecycle, StaleSessionError, token_expiry_epoch


def _jwt(exp: float) -> str:
    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{enc({'alg':'none'})}.{enc({'exp':exp})}.sig"


def test_token_expiry_epoch_reads_jwt_exp_without_returning_token() -> None:
    exp = time.time() + 3600
    assert token_expiry_epoch(_jwt(exp)) == pytest.approx(exp)
    assert token_expiry_epoch("opaque-token") is None


def test_status_reports_freshness_without_secret_values(tmp_path) -> None:
    now = time.time()
    snapshot = BrowserSessionSnapshot(
        captured_at=now - 10,
        access_token=_jwt(now + 3600),
        cookies=[{"name": "__Secure-next-auth.session-token", "value": "SECRET", "domain": "chatgpt.com", "expires": now + 7200}],
        headers={"oai-did": "SECRET-DEVICE", "user-agent": "UA"},
        sentinel={"chat_requirements_token": "SECRET-SENTINEL", "expire_at": now + 120, "consumed": False},
    )
    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: snapshot, now=lambda: now)
    status = lifecycle.status()
    rendered = json.dumps(status, sort_keys=True)
    assert status["state"] == "ready"
    assert status["access_token"]["state"] == "fresh"
    assert status["session_cookie"]["state"] == "fresh"
    assert status["sentinel"]["state"] == "fresh"
    assert "SECRET" not in rendered
    assert "SECRET-DEVICE" not in rendered
    assert "SECRET-SENTINEL" not in rendered


def test_status_marks_expired_access_token_stale() -> None:
    now = time.time()
    snapshot = BrowserSessionSnapshot(captured_at=now - 60, access_token=_jwt(now - 1))
    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: snapshot, now=lambda: now)
    assert lifecycle.status()["state"] == "stale"
    with pytest.raises(StaleSessionError):
        lifecycle.assert_precommit_ready()


def test_unknown_expiry_is_not_falsely_classified_stale() -> None:
    now = time.time()
    snapshot = BrowserSessionSnapshot(captured_at=now, access_token="opaque", cookies=[{"name": "__Secure-next-auth.session-token", "value": "opaque", "domain": "chatgpt.com"}])
    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: snapshot, now=lambda: now)
    status = lifecycle.status()
    assert status["state"] == "ready"
    assert status["access_token"]["state"] == "unknown_expiry"


def test_bootstrap_uses_capture_and_refresh_uses_cffi() -> None:
    now = time.time()
    calls: list[dict] = []
    snapshot = BrowserSessionSnapshot(captured_at=now, access_token=_jwt(now + 600))

    async def capture(**kwargs):
        calls.append(kwargs)
        return snapshot

    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: snapshot, capture=capture, now=lambda: now)
    boot = lifecycle.bootstrap(profile_dir="/tmp/p", browser_mode="displayless", save=True)
    assert boot["state"] == "ready"
    assert len(calls) == 1
    assert calls[0]["save"] is True

    cookie_snapshot = BrowserSessionSnapshot(cookies=[
        {"name": "__Secure-next-auth.session-token", "value": "cookie", "domain": "chatgpt.com"}])
    cffi_lifecycle = SessionLifecycle(
        snapshot_loader=lambda **_: cookie_snapshot, capture=capture, now=lambda: now)
    with patch("re_gpt.session_lifecycle.run_async_sync", return_value="access"), patch(
        "re_gpt.session_lifecycle.save_browser_session_snapshot"):
        ref = cffi_lifecycle.refresh(profile_dir="/tmp/p", browser_mode="displayless")
    assert ref["transport"] == "curl_cffi"
    assert ref["browser_used"] is False
    assert ref["session"]["state"] == "ready"
    assert len(calls) == 1


def test_auto_refresh_uses_cffi_not_browser_capture() -> None:
    now = time.time()
    stale = BrowserSessionSnapshot(
        captured_at=now - 60,
        access_token=_jwt(now - 10),
        cookies=[{"name": "__Secure-next-auth.session-token", "value": "cookie", "domain": "chatgpt.com"}],
    )
    calls: list[dict] = []

    async def capture(**kwargs):
        calls.append(kwargs)
        return stale

    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: stale, capture=capture, now=lambda: now)
    fresh = {"state": "ready", "access_token": {"state": "fresh"}}
    with patch.object(lifecycle, "refresh_cffi", return_value={"session": fresh}) as refresh:
        assert lifecycle.assert_precommit_ready(auto_refresh=True) == fresh
    refresh.assert_called_once()
    assert calls == []


def test_bootstrap_without_save_builds_status_from_captured_snapshot() -> None:
    now = time.time()
    snapshot = BrowserSessionSnapshot(captured_at=now, access_token=_jwt(now + 600))

    async def capture(**_):
        return snapshot

    # snapshot_loader returns None simulating no disk snapshot saved yet
    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: None, capture=capture, now=lambda: now)
    boot = lifecycle.bootstrap(profile_dir="/tmp/p", browser_mode="displayless", save=False)
    assert boot["state"] == "ready"
    assert boot["access_token"]["state"] == "fresh"


def test_doctor_returns_machine_distinct_session_diagnosis() -> None:
    now = time.time()
    snapshot = BrowserSessionSnapshot(captured_at=now, access_token=_jwt(now + 60), sentinel={"consumed": True})
    lifecycle = SessionLifecycle(snapshot_loader=lambda **_: snapshot, now=lambda: now)
    report = lifecycle.doctor()
    assert report["session_state"] == "ready"
    assert report["diagnosis"] in {"ready", "ready_without_fresh_sentinel"}
    assert isinstance(report["recommended_actions"], list)
