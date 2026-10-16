from __future__ import annotations

from chatgpt_dom_relay import (
    DomSubmissionReceipt,
    is_conversation_mutation_request,
    should_retry_dom_with_enter,
)


def test_conversation_post_is_a_mutation_request() -> None:
    assert is_conversation_mutation_request(
        "POST", "https://chatgpt.com/backend-api/conversation"
    ) is True
    assert is_conversation_mutation_request(
        "GET", "https://chatgpt.com/backend-api/conversation/abc"
    ) is False
    assert is_conversation_mutation_request(
        "POST", "https://chatgpt.com/backend-api/sentinel/chat-requirements"
    ) is False


def test_receipt_has_commit_evidence_when_network_request_was_observed() -> None:
    receipt = DomSubmissionReceipt(
        mutation_request_seen=True,
        prompt_rendered=False,
        stop_visible=False,
        composer_cleared=False,
        composer_text="Reply exactly ACK.",
    )
    assert receipt.commit_evidence is True
    assert should_retry_dom_with_enter(receipt, "Reply exactly ACK.") is False


def test_receipt_has_commit_evidence_when_prompt_is_rendered() -> None:
    receipt = DomSubmissionReceipt(
        mutation_request_seen=False,
        prompt_rendered=True,
        stop_visible=False,
        composer_cleared=True,
        composer_text="",
    )
    assert receipt.commit_evidence is True
    assert should_retry_dom_with_enter(receipt, "Reply exactly ACK.") is False


def test_noop_click_can_retry_with_enter_only_when_prompt_remains_unsent() -> None:
    receipt = DomSubmissionReceipt(
        mutation_request_seen=False,
        prompt_rendered=False,
        stop_visible=False,
        composer_cleared=False,
        composer_text="Reply exactly ACK.",
    )
    assert receipt.commit_evidence is False
    assert should_retry_dom_with_enter(receipt, "Reply exactly ACK.") is True


def test_enter_retry_is_refused_if_composer_state_is_ambiguous() -> None:
    receipt = DomSubmissionReceipt(
        mutation_request_seen=False,
        prompt_rendered=False,
        stop_visible=False,
        composer_cleared=True,
        composer_text="",
    )
    assert receipt.commit_evidence is True
    assert should_retry_dom_with_enter(receipt, "Reply exactly ACK.") is False
