from __future__ import annotations

from re_gpt import entrypoint


MUTATION = {
    "latest_assistant": "",
    "resolved_thread_id": "abc",
    "transport": "dom",
    "browser_mode": "displayless",
    "mutation_state": "submitted_unconfirmed",
}


def test_unconfirmed_dom_send_missing_from_graph_returns_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(entrypoint, "submit_prompt", lambda *args, **kwargs: dict(MUTATION))
    monkeypatch.setattr(
        entrypoint,
        "reconcile_conversation",
        lambda *args, **kwargs: {
            "conversation_id": "abc",
            "new_messages": 0,
            "total_messages": 10,
            "json_path": None,
            "asset_count": 0,
            "asset_errors": 0,
            "mutation_prompt_present": False,
            "mutation_assistant": None,
        },
    )

    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])

    assert code == 1
    assert "not found in the canonical graph" in capsys.readouterr().err.lower()


def test_unconfirmed_dom_send_with_user_node_but_no_assistant_returns_two(monkeypatch, capsys) -> None:
    monkeypatch.setattr(entrypoint, "submit_prompt", lambda *args, **kwargs: dict(MUTATION))
    monkeypatch.setattr(
        entrypoint,
        "reconcile_conversation",
        lambda *args, **kwargs: {
            "conversation_id": "abc",
            "new_messages": 1,
            "total_messages": 11,
            "json_path": None,
            "asset_count": 0,
            "asset_errors": 0,
            "mutation_prompt_present": True,
            "mutation_assistant": None,
        },
    )

    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])

    assert code == 2
    assert "do not resend" in capsys.readouterr().err.lower()


def test_unconfirmed_dom_send_is_promoted_when_reconcile_finds_assistant(monkeypatch, capsys) -> None:
    monkeypatch.setattr(entrypoint, "submit_prompt", lambda *args, **kwargs: dict(MUTATION))
    monkeypatch.setattr(
        entrypoint,
        "reconcile_conversation",
        lambda *args, **kwargs: {
            "conversation_id": "abc",
            "new_messages": 2,
            "total_messages": 12,
            "json_path": None,
            "asset_count": 0,
            "asset_errors": 0,
            "mutation_prompt_present": True,
            "mutation_assistant": "ACK",
        },
    )

    code = entrypoint.write_main(["--send", "abc", "--prompt", "hello"])

    assert code == 0
    output = capsys.readouterr().out
    assert "ACK" in output
    assert "reconciled abc" in output
