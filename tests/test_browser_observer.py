from __future__ import annotations

import json
import subprocess
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from re_gpt.browser_observer import (
    BrowserObservationError,
    classify_dom_status,
    fetch_conversation_same_origin,
)
from re_gpt.browser_observer_runner import _dom_status


def test_same_origin_reader_returns_validated_graph(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        payload = {
            "chat": {"mapping": {"root": {"parent": None, "message": None}}},
            "diagnostic": {"adapter": "browser", "method": "GET", "status": 200},
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("re_gpt.browser_observer.subprocess.run", fake_run)
    result = fetch_conversation_same_origin(
        "abc",
        profile_dir=tmp_path / "profile",
        browser_mode="displayless",
        timeout=12,
    )
    assert result.chat["mapping"]
    assert result.diagnostic["adapter"] == "browser"
    command = calls[0][0]
    assert "re_gpt.browser_observer_runner" in command
    assert "--read" in command
    assert "--profile-dir" in command


def test_same_origin_reader_rejects_non_graph_payload(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        payload = {"chat": {"mapping": {}}, "diagnostic": {"status": 200}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("re_gpt.browser_observer.subprocess.run", fake_run)
    with pytest.raises(BrowserObservationError, match="non-empty mapping"):
        fetch_conversation_same_origin("abc")


def test_dom_status_prioritizes_transient_connection_interruption() -> None:
    result = classify_dom_status({
        "loaded": True,
        "generating": True,
        "thinking": True,
        "tool_activity": True,
        "connection_interrupted": True,
        "conversation_load_error": False,
        "history_load_error": False,
        "assistant_text_visible": True,
    })
    assert result["status"] == "connection_interrupted"


def test_dom_status_recognizes_tool_activity_and_settled_state() -> None:
    tool = classify_dom_status({
        "loaded": True,
        "generating": True,
        "thinking": False,
        "tool_activity": True,
        "connection_interrupted": False,
        "conversation_load_error": False,
        "history_load_error": False,
        "assistant_text_visible": True,
    })
    settled = classify_dom_status({
        "loaded": True,
        "generating": False,
        "thinking": False,
        "tool_activity": False,
        "connection_interrupted": False,
        "conversation_load_error": False,
        "history_load_error": False,
        "assistant_text_visible": True,
    })
    assert tool["status"] == "tool_activity"
    assert settled["status"] == "settled"


def test_dom_status_evaluate_source_escapes_javascript_newlines() -> None:
    class Page:
        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

        async def evaluate(self, source, conversation_id):
            assert r"tertiary.join('\n')" in source
            assert r"(^|\n)Retry($|\n)" in source
            return {"conversation_id": conversation_id}

    relay = SimpleNamespace(page=Page(), timeout_ms=1000)
    conversation_id = "6ad2b900-e558-83ec-bb40-3fb1f805e48b"
    result = asyncio.run(_dom_status(relay, conversation_id))
    assert result == {"observation": {"conversation_id": conversation_id}}
