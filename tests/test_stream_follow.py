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


def test_decoder_handles_nested_envelope_and_json_patch_deltas() -> None:
    decoder = SSEProgressDecoder()
    envelope = _data({
        "v": {
            "conversation_id": "c-99",
            "message": {
                "id": "asst-99",
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": ["In quiet lines"]},
                "status": "in_progress",
                "channel": "final",
            },
        },
        "c": 1,
    })
    patch1 = _data({
        "o": "patch",
        "v": [
            {"p": "/message/content/parts/0", "o": "append", "v": " of code"},
        ],
    })
    patch2 = _data({
        "v": [
            {"p": "/message/content/parts/0", "o": "append", "v": ", dreams awaken."},
            {"p": "/message/status", "o": "replace", "v": "finished_successfully"},
            {"p": "/message/end_turn", "o": "replace", "v": True},
        ],
    })
    complete = _data({
        "type": "message_stream_complete",
        "conversation_id": "c-99",
    }) + "data: [DONE]\n\n"

    events = decoder.feed(envelope + patch1 + patch2 + complete)
    deltas = [e["delta"] for e in events if e.get("kind") == "token_delta"]
    assert deltas == ["In quiet lines", " of code", ", dreams awaken."]
    assert decoder.assistant_text == "In quiet lines of code, dreams awaken."
    assert decoder.conversation_id == "c-99"
    assert decoder.assistant_message_id == "asst-99"
    assert decoder.terminal_observed is True


def test_reasoning_and_tool_use_do_not_terminate_early_or_contaminate_assistant_text() -> None:
    decoder = SSEProgressDecoder()
    # 1. First reasoning chunk
    step1 = _data({
        "message": {
            "id": "thought-1",
            "author": {"role": "assistant"},
            "channel": "analysis",
            "content": {"content_type": "thoughts", "parts": ["Let me analyze the proofs."]},
            "status": "in_progress",
        }
    })
    # 2. End of first reasoning block (should NOT terminate!)
    step2 = _data({
        "message": {
            "id": "thought-1",
            "author": {"role": "assistant"},
            "channel": "analysis",
            "content": {"content_type": "thoughts", "parts": ["Let me analyze the proofs."]},
            "status": "finished_successfully",
            "end_turn": True,
        }
    })
    # 3. Tool invocation (should NOT terminate!)
    step3 = _data({
        "message": {
            "id": "tool-call-1",
            "author": {"role": "assistant"},
            "recipient": "dashi_agda_preflight",
            "content": {"content_type": "tool_call", "parts": ["discover()"]},
            "status": "finished_successfully",
            "end_turn": True,
        }
    })
    # 4. Second reasoning block (should NOT terminate!)
    step4 = _data({
        "message": {
            "id": "thought-2",
            "author": {"role": "assistant"},
            "channel": "analysis",
            "content": {"content_type": "thoughts", "parts": ["Verification passed. Generating final response."]},
            "status": "finished_successfully",
            "end_turn": True,
        }
    })
    # Feed steps 1-4
    events1 = decoder.feed(step1 + step2 + step3 + step4)
    assert decoder.terminal_observed is False
    assert decoder.assistant_text == ""
    assert "Verification passed" in decoder.thinking_text

    # 5. Final response
    step5 = _data({
        "message": {
            "id": "asst-final",
            "author": {"role": "assistant"},
            "channel": "final",
            "content": {"content_type": "text", "parts": ["The proof is complete with 0 errors."]},
            "status": "finished_successfully",
            "end_turn": True,
        }
    }) + "data: [DONE]\n\n"

    events2 = decoder.feed(step5)
    assert decoder.terminal_observed is True
    assert decoder.assistant_text == "The proof is complete with 0 errors."


