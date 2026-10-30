from unittest.mock import patch
import pytest
from re_gpt.session_lifecycle import SessionLifecycle, StaleSessionError
from re_gpt.browser_session_replay import BrowserSessionSnapshot


def test_missing_snapshot_fails_before_network():
    with pytest.raises(StaleSessionError, match="No owned session"):
        SessionLifecycle(snapshot_loader=lambda **kw: None).refresh_cffi()


def test_missing_cookie_fails_before_network():
    snap = BrowserSessionSnapshot(access_token="old", cookies=[])
    with pytest.raises(StaleSessionError, match="complete session cookie"):
        SessionLifecycle(snapshot_loader=lambda **kw: snap).refresh_cffi()


def test_successful_cffi_renewal_persists_only_after_success():
    snap = BrowserSessionSnapshot(cookies=[
        {"name": "__Secure-next-auth.session-token", "value": "cookie", "domain": "chatgpt.com"}])
    service = SessionLifecycle(snapshot_loader=lambda **kw: snap, now=lambda: 1000)
    with patch("re_gpt.session_lifecycle.run_async_sync", return_value="access") as runner, patch(
        "re_gpt.session_lifecycle.save_browser_session_snapshot") as save:
        receipt = service.refresh_cffi()
    assert receipt["transport"] == "curl_cffi"
    assert receipt["browser_used"] is False
    assert receipt["session"]["access_token"]["present"] is True
    assert "access" not in receipt["session"].get("token_value", "")
    saved = save.call_args.args[0]
    assert saved.access_token == "access"
    assert saved.captured_at == 1000


def test_failed_cffi_does_not_overwrite_snapshot():
    snap = BrowserSessionSnapshot(cookies=[
        {"name": "__Secure-next-auth.session-token", "value": "cookie", "domain": "chatgpt.com"}])
    with patch("re_gpt.session_lifecycle.run_async_sync", side_effect=RuntimeError("auth rejected")), patch(
        "re_gpt.session_lifecycle.save_browser_session_snapshot") as save:
        with pytest.raises(RuntimeError, match="auth rejected"):
            SessionLifecycle(snapshot_loader=lambda **kw: snap).refresh_cffi()
    save.assert_not_called()
