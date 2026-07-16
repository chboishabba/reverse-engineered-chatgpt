from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatgpt_dom_relay import (
    assistant_reply_after_prompt,
    build_conversation_url,
    extract_thread_id,
    latest_assistant_from_sse,
    load_alias_map,
    resolve_prompt_text,
    resolve_thread_selector,
    summarize_snapshot,
)


def test_extract_thread_id_from_url() -> None:
    assert (
        extract_thread_id("https://chatgpt.com/c/6a33ae58-cb84-83ec-b187-ddab3179ccbb")
        == "6a33ae58-cb84-83ec-b187-ddab3179ccbb"
    )


def test_resolve_thread_selector_prefers_uuid() -> None:
    alias_map = {"ns": "11111111-1111-1111-1111-111111111111"}
    assert (
        resolve_thread_selector("6a3ca60a-fe1c-83ec-867b-ac0c6698acd8", alias_map)
        == "6a3ca60a-fe1c-83ec-867b-ac0c6698acd8"
    )


def test_load_alias_map_and_resolve_alias(tmp_path: Path) -> None:
    path = tmp_path / "threads.json"
    path.write_text(
        json.dumps(
            {
                "NS": "6a33ae58-cb84-83ec-b187-ddab3179ccbb",
                "YM": "https://chatgpt.com/c/6a3ca60a-fe1c-83ec-867b-ac0c6698acd8",
            }
        ),
        encoding="utf-8",
    )
    alias_map = load_alias_map([path])
    assert resolve_thread_selector("ns", alias_map) == "6a33ae58-cb84-83ec-b187-ddab3179ccbb"
    assert resolve_thread_selector("YM", alias_map) == "6a3ca60a-fe1c-83ec-867b-ac0c6698acd8"


def test_resolve_prompt_text_prefers_explicit_prompt(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from-file", encoding="utf-8")
    assert (
        resolve_prompt_text(
            prompt="from-flag",
            prompt_file=None,
            stdin_text="from-stdin",
            stdin_is_tty=False,
        )
        == "from-flag"
    )
    assert (
        resolve_prompt_text(
            prompt=None,
            prompt_file=prompt_file,
            stdin_text="from-stdin",
            stdin_is_tty=False,
        )
        == "from-file"
    )


def test_resolve_prompt_text_uses_stdin_when_present() -> None:
    assert (
        resolve_prompt_text(
            prompt=None,
            prompt_file=None,
            stdin_text="from-stdin",
            stdin_is_tty=False,
        )
        == "from-stdin"
    )


def test_build_conversation_url_normalizes_thread() -> None:
    assert (
        build_conversation_url("6A33AE58-CB84-83EC-B187-DDAB3179CCBB")
        == "https://chatgpt.com/c/6a33ae58-cb84-83ec-b187-ddab3179ccbb"
    )


def test_summarize_snapshot_extracts_latest_turns() -> None:
    payload = summarize_snapshot(
        {
            "conversation_id": "abc",
            "url": "https://chatgpt.com/c/abc",
            "title": "Demo",
            "turns": [
                {"role": "user", "text": "first"},
                {"role": "assistant", "text": "reply one"},
                {"role": "user", "text": "second"},
                {"role": "assistant", "text": "reply two"},
            ],
        }
    )
    assert payload["assistant_turn_count"] == 2
    assert payload["user_turn_count"] == 2
    assert payload["latest_assistant"] == "reply two"
    assert payload["latest_user"] == "second"


def test_assistant_reply_after_prompt_ignores_hydrated_history() -> None:
    snapshot = {
        "turns": [
            {"role": "user", "text": "old question"},
            {"role": "assistant", "text": "old answer"},
            {"role": "user", "text": "Knock knock"},
            {"role": "assistant", "text": "Who's there?"},
        ]
    }
    assert assistant_reply_after_prompt(snapshot, "Knock knock") == "Who's there?"
    assert assistant_reply_after_prompt(snapshot, "new question") is None


def test_latest_assistant_from_sse_uses_final_assistant_event() -> None:
    raw = "\n".join(
        [
            'data: {"message":{"author":{"role":"assistant"},"content":{"parts":["first"]}}}',
            'data: {"message":{"author":{"role":"assistant"},"content":{"parts":["final"]}}}',
            "data: [DONE]",
        ]
    )
    assert latest_assistant_from_sse(raw) == "final"


def test_resolve_thread_selector_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError):
        resolve_thread_selector("missing", {})
