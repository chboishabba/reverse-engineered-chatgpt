from __future__ import annotations

from re_gpt.mutation_receipt import classify_prompt_mutation


PROMPT = "Reply exactly ACK."


def _message(role: str, text: str, create_time: float) -> dict:
    return {
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [text]},
        "create_time": create_time,
    }


def test_old_identical_prompt_does_not_certify_new_mutation() -> None:
    chat = {
        "mapping": {
            "old-user": {
                "message": _message("user", PROMPT, 100.0),
                "parent": "root",
                "children": ["old-assistant"],
            },
            "old-assistant": {
                "message": _message("assistant", "old ACK", 101.0),
                "parent": "old-user",
                "children": [],
            },
        }
    }

    receipt = classify_prompt_mutation(chat, PROMPT, not_before=200.0)

    assert receipt.prompt_committed is False
    assert receipt.user_node_ids == ()
    assert receipt.assistant_text is None


def test_new_user_node_certifies_mutation_and_finds_assistant_child() -> None:
    chat = {
        "mapping": {
            "new-user": {
                "message": _message("user", PROMPT, 205.0),
                "parent": "root",
                "children": ["new-assistant"],
            },
            "new-assistant": {
                "message": _message("assistant", "ACK", 206.0),
                "parent": "new-user",
                "children": [],
            },
        }
    }

    receipt = classify_prompt_mutation(chat, PROMPT, not_before=200.0)

    assert receipt.prompt_committed is True
    assert receipt.user_node_ids == ("new-user",)
    assert receipt.assistant_text == "ACK"


def test_quoted_prompt_inside_assistant_text_is_not_a_user_mutation() -> None:
    chat = {
        "mapping": {
            "assistant": {
                "message": _message(
                    "assistant",
                    f"Earlier logs contained: {PROMPT}",
                    205.0,
                ),
                "parent": "root",
                "children": [],
            }
        }
    }

    receipt = classify_prompt_mutation(chat, PROMPT, not_before=200.0)

    assert receipt.prompt_committed is False
    assert receipt.user_node_ids == ()


def test_committed_prompt_without_assistant_is_distinct_from_no_commit() -> None:
    chat = {
        "mapping": {
            "new-user": {
                "message": _message("user", PROMPT, 205.0),
                "parent": "root",
                "children": [],
            }
        }
    }

    receipt = classify_prompt_mutation(chat, PROMPT, not_before=200.0)

    assert receipt.prompt_committed is True
    assert receipt.assistant_text is None


def test_reasoning_model_intermediate_thoughts_node_resolves_final_assistant_text() -> None:
    chat = {
        "mapping": {
            "new-user": {
                "message": _message("user", PROMPT, 205.0),
                "parent": "root",
                "children": ["thoughts-node"],
            },
            "thoughts-node": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "thoughts", "thoughts": [{"summary": "thinking"}]},
                    "create_time": 205.5,
                },
                "parent": "new-user",
                "children": ["final-assistant"],
            },
            "final-assistant": {
                "message": _message("assistant", "ACK.", 206.0),
                "parent": "thoughts-node",
                "children": [],
            },
        }
    }

    receipt = classify_prompt_mutation(chat, PROMPT, not_before=200.0)

    assert receipt.prompt_committed is True
    assert receipt.user_node_ids == ("new-user",)
    assert receipt.assistant_text == "ACK."


def test_rendered_prompt_and_assistant_reply_with_attachment_chip() -> None:
    from chatgpt_dom_relay import assistant_reply_after_rendered_text
    from re_gpt.dom_commit import rendered_prompt_count

    page_text = (
        "You said:\n"
        "accept_attachment.txt\n"
        "File\n"
        "Reply exactly REGPT-V1-ATTACHMENT-123.\n"
        "ChatGPT said:\n"
        "REGPT-V1-ATTACHMENT-123.\n"
        "ChatGPT can make mistakes."
    )

    assert rendered_prompt_count(page_text, "Reply exactly REGPT-V1-ATTACHMENT-123.") == 1
    assert (
        assistant_reply_after_rendered_text(
            page_text, "Reply exactly REGPT-V1-ATTACHMENT-123."
        )
        == "REGPT-V1-ATTACHMENT-123."
    )

