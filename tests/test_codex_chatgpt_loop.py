from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.codex_chatgpt_loop import (
    build_feedback_prompt,
    load_pair,
    parse_thread_id,
)
from chatgpt_dom_relay import latest_assistant_from_sse


def test_parse_thread_id_from_codex_jsonl() -> None:
    output = '\n'.join([
        '{"type":"turn.started"}',
        '{"type":"thread.started","thread_id":"thread-123"}',
    ])
    assert parse_thread_id(output) == "thread-123"


def test_feedback_prompt_contains_external_response() -> None:
    prompt = build_feedback_prompt("Use the existing relay.")
    assert "Use the existing relay." in prompt
    assert "concrete changes" in prompt


def test_load_pair_mapping() -> None:
    codex_id, chatgpt_id = load_pair(ROOT / "codex_chatgpt_threads.json", "ns")
    assert codex_id == "019f4a4f-776e-75e1-91fa-f8f7a5d6d939"
    assert chatgpt_id == "6a33ae58-cb84-83ec-b187-ddab3179ccbb"


def test_sse_parser_extracts_latest_assistant_message() -> None:
    raw = '\n'.join([
        'data: ' + json.dumps({"message": {"author": {"role": "assistant"}, "content": {"parts": ["one"]}}}),
        'data: ' + json.dumps({"message": {"author": {"role": "assistant"}, "content": {"parts": ["two"]}}}),
    ])
    assert latest_assistant_from_sse(raw) == "two"
