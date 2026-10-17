from __future__ import annotations

import json

from re_gpt.stream_follow import SSEProgressDecoder


def _data(payload) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def test_decoder_emits_incremental_token_deltas_across_split_chunks() -> None:
    decoder = SSEProgressDecoder()
    first = _data({
        "conversation_id": "conv-1",
        "message": {
            "id": "assistant-1",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["hel"]},
            "status": "in_progress",
            "end_turn": False,
        },
    })
    second = _data({
        "conversation_id": "conv-1",
        "message": {
            "id": "assistant-1",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["hello"]},
            "status": "in_progress",
            "end_turn": False,
        },
    })
    wire = (first + second).encode()
    events = []
    events.extend(decoder.feed(wire[:37]))
    events.extend(decoder.feed(wire[37:]))
    deltas = [event["delta"] for event in events if event["kind"] == "token_delta"]
    assert deltas == ["hel", "lo"]
    assert decoder.assistant_text == "hello"
    assert decoder.conversation_id == "conv-1"


def test_decoder_classifies_thinking_tool_and_terminal_events() -> None:
    decoder = SSEProgressDecoder()
    thinking = _data({
        "message": {
            "id": "a1",
            "author": {"role": "assistant"},
            "channel": "analysis",
            "content": {"content_type": "thoughts", "parts": ["checking"]},
            "status": "in_progress",
        }
    })
    tool = _data({
        "message": {
            "id": "tool-1",
            "author": {"role": "assistant"},
            "recipient": "web.run",
            "content": {"content_type": "tool_call", "parts": []},
            "status": "in_progress",
        }
    })
    terminal = _data({
        "message": {
            "id": "a1",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["done"]},
            "status": "finished_successfully",
            "end_turn": True,
        }
    }) + "data: [DONE]\n\n"
    events = decoder.feed(thinking + tool + terminal)
    kinds = [event["kind"] for event in events]
    assert "thinking" in kinds
    assert "tool_invocation" in kinds
    assert "terminal" in kinds
    assert decoder.terminal_observed is True
    assert decoder.terminal_reason in {"finished_successfully", "done"}
