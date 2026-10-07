from __future__ import annotations

import io
from pathlib import Path

import pytest

from re_gpt import entrypoint


def test_resolve_prompt_prefers_flag(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from file", encoding="utf-8")
    assert entrypoint.resolve_prompt("from flag", None, stdin=io.StringIO("stdin")) == "from flag"


def test_resolve_prompt_rejects_flag_and_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from file", encoding="utf-8")
    with pytest.raises(ValueError, match="either --prompt or --prompt-file"):
        entrypoint.resolve_prompt("from flag", prompt_file, stdin=io.StringIO(""))


def test_write_main_reconciles_after_successful_mutation(monkeypatch, capsys) -> None:
    order: list[str] = []

    monkeypatch.setattr(
        entrypoint,
        "submit_prompt",
        lambda *args, **kwargs: order.append("mutate")
        or {
            "latest_assistant": "ACK",
            "resolved_thread_id": "abc",
            "transport": "api",
            "browser_mode": "displayless",
        },
    )
    monkeypatch.setattr(
        entrypoint,
        "reconcile_conversation",
        lambda *args, **kwargs: order.append("reconcile")
        or {
            "conversation_id": "abc",
            "new_messages": 2,
            "total_messages": 10,
            "json_path": None,
            "asset_count": 0,
            "asset_errors": 0,
        },
    )

    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])
    assert code == 0
    assert order == ["mutate", "reconcile"]
    output = capsys.readouterr().out
    assert "ACK" in output
    assert "reconciled abc" in output


def test_write_main_returns_two_when_mutation_succeeds_but_reconcile_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        entrypoint,
        "submit_prompt",
        lambda *args, **kwargs: {
            "latest_assistant": "ACK",
            "resolved_thread_id": "abc",
            "transport": "dom",
            "browser_mode": "displayless",
        },
    )

    def fail_reconcile(*args, **kwargs):
        raise RuntimeError("archive unavailable")

    monkeypatch.setattr(entrypoint, "reconcile_conversation", fail_reconcile)
    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])
    assert code == 2
    assert "do not resend" in capsys.readouterr().err.lower()
