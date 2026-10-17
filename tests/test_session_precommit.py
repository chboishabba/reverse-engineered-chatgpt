from __future__ import annotations

import base64
import json
import time

import pytest

from re_gpt.browser_session_replay import BrowserSessionSnapshot, save_browser_session_snapshot
from re_gpt.mutation_service import MutationService
from re_gpt.session_lifecycle import StaleSessionError


def _jwt(exp: float) -> str:
    raw = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"x.{raw}.y"


def test_mutation_service_rejects_provably_stale_saved_session_before_transport(monkeypatch, tmp_path) -> None:
    snapshot = BrowserSessionSnapshot(captured_at=time.time() - 60, access_token=_jwt(time.time() - 10), profile_dir=str(tmp_path))
    save_browser_session_snapshot(snapshot, profile_dir=tmp_path)

    called = False

    def should_not_dispatch(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("transport dispatched")

    monkeypatch.setattr("re_gpt.mutation_service.submit_async_prompt_once", should_not_dispatch)
    service = MutationService()
    with pytest.raises(StaleSessionError):
        service.continue_thread("conversation-id", "hello", profile_dir=tmp_path, write_transport="async")
    assert called is False
