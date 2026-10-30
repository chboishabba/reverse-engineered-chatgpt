"""Offline activity visibility checks. No account or network use."""
from re_gpt.terminal_activity import activity_from_progress, redact


def test_preserve_observed_tool_payload_and_mask_keyed_secrets():
    observed = {"kind": "tool_invocation", "recipient": "github",
                "message_id": "node-1",
                "body": "tool invoked",
                "arguments": {"repository": "owner/repo", "api_key": "private"}}
    event = activity_from_progress(observed)
    assert event["body"] == "tool invoked"
    assert event["arguments"]["repository"] == "owner/repo"
    assert event["arguments"]["api_key"] == "[redacted]"


def test_private_reasoning_never_fabricated():
    event = activity_from_progress({"kind": "thinking", "message_id": "node"})
    assert event["body"] is None
    assert "unavailable" in event["summary"]


def test_non_activity_deltas_not_recorded_as_tools():
    assert activity_from_progress({"kind": "token_delta", "delta": "answer"}) is None


def test_nested_redaction():
    assert redact({"config": [{"cookie": "secret", "name": "public"}]}) == {
        "config": [{"cookie": "[redacted]", "name": "public"}]}
