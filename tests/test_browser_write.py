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


def test_submit_prompt_uses_accessibility_browser_auto_transport(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(command, *, input, text, capture_output, check):
        calls.append((command, input))
        return _Completed(
            0,
            json.dumps(
                {
                    "latest_assistant": "done",
                    "resolved_thread_id": "abc",
                    "transport": "api",
                    "browser_mode": "displayless",
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = submit_prompt(
        "abc",
        "hello",
        profile_dir=tmp_path / "profile",
    )

    assert result["latest_assistant"] == "done"
    assert result["browser_mode"] == "displayless"
    assert len(calls) == 1
    command, stdin_text = calls[0]
    assert stdin_text == "hello"
    assert command[:3] == [command[0], "-m", "re_gpt.accessibility_browser"]
    assert command[command.index("--transport") + 1] == "auto"
    assert command[command.index("--browser-mode") + 1] == "displayless"


def test_submit_prompt_can_select_unified_headless(monkeypatch) -> None:
    observed: list[str] = []

    def fake_run(command, *, input, text, capture_output, check):
        observed.append(command[command.index("--browser-mode") + 1])
        return _Completed(
            0,
            json.dumps(
                {
                    "latest_assistant": "done",
                    "resolved_thread_id": "abc",
                    "transport": "dom",
                    "browser_mode": "headless",
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    submit_prompt("abc", "hello", browser_mode="headless")
    assert observed == ["headless"]


def test_submit_prompt_reports_browser_failure(monkeypatch) -> None:
    def fake_run(command, *, input, text, capture_output, check):
        return _Completed(1, stderr="browser failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(BrowserWriteError, match="browser failed"):
        submit_prompt("abc", "hello")


def test_submit_prompt_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt is empty"):
        submit_prompt("abc", "   ")
