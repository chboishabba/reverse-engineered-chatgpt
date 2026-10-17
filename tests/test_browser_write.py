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


def test_submit_prompt_uses_dom_only_mutation_runner(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(command, *, input, text, capture_output, check):
        calls.append((command, input))
        return _Completed(0, json.dumps({
            "mutation_state": "submission_observed",
            "latest_assistant": "",
            "resolved_thread_id": "abc",
            "transport": "dom",
            "browser_mode": "displayless",
            "authenticated": True,
            "hydrated": True,
        }))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = submit_prompt("abc", "hello", profile_dir=tmp_path / "profile")
    assert result["mutation_state"] == "submission_observed"
    command, stdin_text = calls[0]
    assert stdin_text == "hello"
    assert command[:3] == [command[0], "-m", "re_gpt.mutation_browser"]
    assert "--transport" not in command
    assert "--follow" not in command


def test_submit_prompt_forwards_repeatable_attachments(monkeypatch, tmp_path: Path) -> None:
    observed: list[list[str]] = []

    def fake_run(command, *, input, text, capture_output, check):
        observed.append(command)
        return _Completed(0, json.dumps({"mutation_state": "submission_observed"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    one = tmp_path / "one.md"
    two = tmp_path / "two.csv"
    submit_prompt("abc", "hello", attachments=[one, two])
    command = observed[0]
    indices = [i for i, item in enumerate(command) if item == "--attach"]
    assert len(indices) == 2
    assert command[indices[0] + 1].endswith("one.md")
    assert command[indices[1] + 1].endswith("two.csv")


def test_submit_prompt_follow_is_explicit(monkeypatch) -> None:
    observed: list[list[str]] = []

    def fake_run(command, *, input, text, capture_output, check):
        observed.append(command)
        return _Completed(0, json.dumps({"latest_assistant": "done"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    submit_prompt("abc", "hello", follow=True)
    assert "--follow" in observed[0]


def test_submit_prompt_can_select_unified_headless(monkeypatch) -> None:
    observed: list[str] = []

    def fake_run(command, *, input, text, capture_output, check):
        observed.append(command[command.index("--browser-mode") + 1])
        return _Completed(0, json.dumps({"transport": "dom", "browser_mode": "headless"}))

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
