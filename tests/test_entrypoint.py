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


def test_write_parser_is_api_first_by_default() -> None:
    args = entrypoint.build_write_parser().parse_args(["--send", "abc", "--prompt", "hello"])
    assert args.write_transport == "async"
    assert args.dom_fallback is True


def test_write_main_reports_complete_operation(monkeypatch, capsys) -> None:
    seen = {}

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, *args, **kwargs):
            seen.update(kwargs)
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
    assert seen["write_transport"] == "async"
    assert seen["dom_fallback"] is True
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


def test_new_routes_to_mutation_service(monkeypatch, capsys) -> None:
    seen = {}

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def new_conversation(self, prompt, **kwargs):
            seen.update({"prompt": prompt, **kwargs})
            return {
                "operation_id": "op-new",
                "conversation_id": "new-id",
                "state": "complete",
                "user_node_id": "u1",
                "assistant_node_id": "a1",
                "assistant_text": "NEW-ACK",
            }

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    code = entrypoint.write_main(["--new", "--prompt", "hello"])
    assert code == 0
    assert seen["prompt"] == "hello"
    assert "conversation=new-id" in capsys.readouterr().out


def test_branch_regenerate_and_edit_route_exact_node_ids(monkeypatch) -> None:
    calls = []

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def branch_from_node(self, conversation, parent, prompt, **kwargs):
            calls.append(("branch", conversation, parent, prompt))
            return {"operation_id": "b", "conversation_id": conversation, "state": "committed_pending", "user_node_id": "u"}

        def regenerate(self, conversation, user_node, **kwargs):
            calls.append(("regenerate", conversation, user_node))
            return {"operation_id": "r", "conversation_id": conversation, "state": "committed_pending", "user_node_id": user_node}

        def edit_and_branch(self, conversation, user_node, prompt, **kwargs):
            calls.append(("edit", conversation, user_node, prompt))
            return {"operation_id": "e", "conversation_id": conversation, "state": "committed_pending", "user_node_id": "u2"}

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    assert entrypoint.write_main(["--branch", "c1", "--parent-node", "a0", "--prompt", "branch"] ) == 0
    assert entrypoint.write_main(["--regenerate", "c1", "--user-node", "u0"] ) == 0
    assert entrypoint.write_main(["--edit-branch", "c1", "--user-node", "u0", "--prompt", "replacement"] ) == 0
    assert calls == [
        ("branch", "c1", "a0", "branch"),
        ("regenerate", "c1", "u0"),
        ("edit", "c1", "u0", "replacement"),
    ]


def test_write_main_streams_tokens_and_writes_output_file(monkeypatch, tmp_path: Path, capsys) -> None:
    seen = {}
    output_path = tmp_path / "sub" / "reply.md"

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, *args, **kwargs):
            seen.update(kwargs)
            cb = kwargs.get("progress_callback")
            if cb:
                cb({"kind": "token_delta", "delta": "part1 "})
                cb({"kind": "token_delta", "delta": "part2"})
            return {
                "operation_id": "op-stream",
                "conversation_id": "c1",
                "state": "complete",
                "user_node_id": "u1",
                "assistant_node_id": "a1",
                "assistant_text": "part1 part2",
            }

    monkeypatch.setattr(entrypoint, "MutationService", FakeService)
    code = entrypoint.write_main([
        "--send", "c1",
        "--prompt", "test",
        "--stream",
        "--output", str(output_path),
    ])
    assert code == 0
    assert seen["follow"] is True
    assert callable(seen["progress_callback"])

    # Output file contains full reply text
    assert output_path.read_text(encoding="utf-8") == "part1 part2\n"

    # stdout captures streamed tokens followed by completion metadata
    out = capsys.readouterr().out
    assert "part1 part2" in out
    assert "[re-gpt] complete" in out


def test_top_level_convenience_functions(monkeypatch, tmp_path: Path) -> None:
    from re_gpt import continue_thread, new_conversation
    output_file = tmp_path / "direct.md"

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def continue_thread(self, conversation, prompt, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                cb({"kind": "token_delta", "delta": "DIRECT-ACK"})
            return {"conversation_id": conversation, "state": "complete", "assistant_text": "DIRECT-ACK"}

        def new_conversation(self, prompt, **kwargs):
            return {"conversation_id": "new-conv", "state": "complete", "assistant_text": "NEW-CONV-ACK"}

    monkeypatch.setattr("re_gpt.mutation_service.MutationService", FakeService)

    res1 = continue_thread("c-direct", "hello", stream=True, output=output_file)
    assert res1["state"] == "complete"
    assert output_file.read_text(encoding="utf-8") == "DIRECT-ACK\n"

    out2 = tmp_path / "new.md"
    res2 = new_conversation("start", output=out2)
    assert res2["conversation_id"] == "new-conv"
    assert out2.read_text(encoding="utf-8") == "NEW-CONV-ACK\n"

