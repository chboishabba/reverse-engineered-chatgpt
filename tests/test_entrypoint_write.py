from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from re_gpt import entrypoint


def test_resolve_prompt_prefers_explicit_text(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from file", encoding="utf-8")
    assert entrypoint.resolve_prompt("explicit", prompt_file, stdin=io.StringIO("stdin")) == "explicit"


def test_resolve_prompt_uses_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from file\n", encoding="utf-8")
    assert entrypoint.resolve_prompt(None, prompt_file, stdin=io.StringIO("stdin")) == "from file"


def test_resolve_prompt_rejects_prompt_and_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from file", encoding="utf-8")
    with pytest.raises(ValueError, match="either --prompt or --prompt-file"):
        entrypoint.resolve_prompt("explicit", prompt_file, stdin=io.StringIO())


def test_write_main_submits_then_reconciles(monkeypatch, capsys) -> None:
    events: list[tuple[str, str]] = []

    def fake_submit(selector, prompt, **kwargs):
        events.append(("submit", selector))
        return {
            "latest_assistant": "assistant reply",
            "resolved_thread_id": "resolved-id",
            "transport": "api",
        }

    def fake_reconcile(conversation_id, **kwargs):
        events.append(("reconcile", conversation_id))
        return {"new_messages": 2, "total_messages": 12}

    monkeypatch.setattr(entrypoint, "submit_prompt", fake_submit)
    monkeypatch.setattr(entrypoint, "reconcile_conversation", fake_reconcile)

    rc = entrypoint.write_main(["--send", "alias", "--prompt", "hello"])

    assert rc == 0
    assert events == [("submit", "alias"), ("reconcile", "resolved-id")]
    out = capsys.readouterr().out
    assert "assistant reply" in out
    assert "reconciled resolved-id" in out


def test_write_main_does_not_reconcile_failed_mutation(monkeypatch) -> None:
    def fake_submit(selector, prompt, **kwargs):
        raise entrypoint.BrowserWriteError("no transport")

    reconciled = False

    def fake_reconcile(conversation_id, **kwargs):
        nonlocal reconciled
        reconciled = True
        return {}

    monkeypatch.setattr(entrypoint, "submit_prompt", fake_submit)
    monkeypatch.setattr(entrypoint, "reconcile_conversation", fake_reconcile)

    rc = entrypoint.write_main(["--send", "alias", "--prompt", "hello"])

    assert rc == 1
    assert reconciled is False


def test_main_delegates_when_send_flag_absent(monkeypatch) -> None:
    called = False

    def fake_legacy_main():
        nonlocal called
        called = True

    monkeypatch.setattr(entrypoint, "legacy_main", fake_legacy_main)
    monkeypatch.setattr(sys, "argv", ["re-gpt", "--list"])

    assert entrypoint.main() == 0
    assert called is True
