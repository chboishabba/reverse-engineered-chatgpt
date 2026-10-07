from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from re_gpt.browser_write import BrowserWriteError, submit_prompt


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_submit_prompt_prefers_api_transport(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(command, *, input, text, capture_output, check):
        calls.append((command, input))
        payload = {
            "latest_assistant": "done",
            "resolved_thread_id": "abc",
            "transport": "api",
        }
        return _Completed(0, json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = submit_prompt(
        "abc",
        "hello",
        relay_path=tmp_path / "chatgpt_dom_relay.py",
        headed=True,
    )

    assert result["latest_assistant"] == "done"
    assert result["transport"] == "api"
    assert len(calls) == 1
    command, stdin_text = calls[0]
    assert stdin_text == "hello"
    assert command[command.index("--transport") + 1] == "api"
    assert "--headed" in command
    assert "--json" in command


def test_submit_prompt_falls_back_to_dom(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_run(command, *, input, text, capture_output, check):
        transport = command[command.index("--transport") + 1]
        calls.append(transport)
        if transport == "api":
            return _Completed(1, stderr="sentinel rejected")
        return _Completed(
            0,
            json.dumps(
                {
                    "latest_assistant": "dom reply",
                    "resolved_thread_id": "abc",
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = submit_prompt(
        "abc",
        "hello",
        relay_path=tmp_path / "chatgpt_dom_relay.py",
    )

    assert calls == ["api", "dom"]
    assert result["latest_assistant"] == "dom reply"
    assert result["transport"] == "dom"
    assert result["fallback_from"] == "api"


def test_submit_prompt_reports_both_transport_failures(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, *, input, text, capture_output, check):
        transport = command[command.index("--transport") + 1]
        return _Completed(1, stderr=f"{transport} failed")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BrowserWriteError) as excinfo:
        submit_prompt(
            "abc",
            "hello",
            relay_path=tmp_path / "chatgpt_dom_relay.py",
        )

    message = str(excinfo.value)
    assert "api failed" in message
    assert "dom failed" in message


def test_submit_prompt_rejects_empty_prompt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt is empty"):
        submit_prompt("abc", "   ", relay_path=tmp_path / "chatgpt_dom_relay.py")
