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


def test_write_main_reports_complete_operation(monkeypatch, capsys) -> None:
    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, *args, **kwargs):
            return {
                "operation_id": "op-1",
                "conversation_id": "abc",
                "state": "complete",
                "user_node_id": "u1",
                "assistant_node_id": "a1",
                "assistant_text": "ACK",
            }

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])
    assert code == 0
    output = capsys.readouterr().out
    assert "ACK" in output
    assert "operation=op-1" in output


def test_write_main_committed_pending_is_successful_and_non_resendable(monkeypatch, capsys) -> None:
    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, *args, **kwargs):
            return {
                "operation_id": "op-2",
                "conversation_id": "abc",
                "state": "committed_pending",
                "user_node_id": "u2",
                "assistant_node_id": None,
                "assistant_text": None,
            }

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])
    assert code == 0
    assert "committed_pending" in capsys.readouterr().out


def test_write_main_returns_two_when_commit_is_unknown(monkeypatch, capsys) -> None:
    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, *args, **kwargs):
            return {"operation_id": "op-3", "conversation_id": "abc", "state": "commit_unknown"}

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])
    assert code == 2
    assert "do not resend" in capsys.readouterr().err.lower()
