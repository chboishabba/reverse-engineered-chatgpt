from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import json


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pull_to_structurer.py"
SPEC = importlib.util.spec_from_file_location("pull_to_structurer_progress", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_fetch_progress_reporter_emits_eta_and_rate(capsys):
    reporter = MODULE.FetchProgressReporter(total=4, engine="async", enabled=True)
    reporter.started -= 3.0
    reporter.start_target()
    reporter.finish_target(ok=True, message_count=10)
    reporter.finish()

    err = capsys.readouterr().err
    assert "[fetch:async]" in err
    assert "done=1/4" in err
    assert "rate=" in err
    assert "elapsed=" in err
    assert "eta=" in err


def test_fetch_progress_reporter_can_be_disabled(capsys):
    reporter = MODULE.FetchProgressReporter(total=2, engine="sync", enabled=False)
    reporter.start_target()
    reporter.finish_target(ok=False, message_count=0)
    reporter.finish()
    captured = capsys.readouterr()
    assert captured.err == ""


def _chat_payload(conversation_id):
    payload = {
        "title": "Identity Test",
        "mapping": {
            "node-1": {
                "message": {
                    "id": "msg-1",
                    "author": {"role": "user"},
                    "content": {"parts": ["hello"]},
                    "create_time": 1,
                }
            }
        },
    }
    if conversation_id is not None:
        payload["id"] = conversation_id
    return payload


class _FakeSyncChatGPT:
    payload = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def fetch_conversation(self, conversation_id):
        return dict(self.payload)


def _fetch_one(monkeypatch, payload, *, unsafe=False):
    _FakeSyncChatGPT.payload = payload
    monkeypatch.setattr(MODULE, "SyncChatGPT", _FakeSyncChatGPT)
    return MODULE._fetch_sync(
        "token",
        [MODULE.FetchTarget("requested-id", "Requested", None)],
        rate_limit_rps=0,
        extract_text=lambda content: "\n".join(content.get("parts") or []),
        debug=False,
        unsafe_allow_unverified_payload_id=unsafe,
    )[0]


def test_fetch_matching_payload_id_flattens_verified_thread(monkeypatch):
    result = _fetch_one(monkeypatch, _chat_payload("requested-id"))

    assert result.ok is True
    assert result.payload_conversation_id == "requested-id"
    assert result.identity_verified is True
    flattened = MODULE._flatten_for_ingest([result])
    assert len(flattened) == 1
    assert flattened[0]["thread_id"] == "requested-id"
    assert flattened[0]["identity_verified"] is True
    provenance = json.loads(flattened[0]["provenance_json"])
    assert provenance["chatgpt_payload_identity"]["identity_verified"] is True


def test_fetch_mismatched_payload_id_fails_closed(monkeypatch):
    result = _fetch_one(monkeypatch, _chat_payload("other-id"))

    assert result.ok is False
    assert result.message_count == 0
    assert "identity mismatch" in result.error
    assert MODULE._flatten_for_ingest([result]) == []


def test_fetch_missing_payload_id_fails_closed(monkeypatch):
    result = _fetch_one(monkeypatch, _chat_payload(None))

    assert result.ok is False
    assert result.message_count == 0
    assert "identity is missing" in result.error
    assert MODULE._flatten_for_ingest([result]) == []


def test_unsafe_unverified_payload_id_can_flatten_with_provenance(monkeypatch):
    result = _fetch_one(monkeypatch, _chat_payload(None), unsafe=True)

    assert result.ok is True
    assert result.identity_verified is False
    flattened = MODULE._flatten_for_ingest(
        [result],
        unsafe_allow_unverified_payload_id=True,
    )
    assert flattened[0]["thread_id"] == "requested-id"
    assert flattened[0]["requested_conversation_id"] == "requested-id"
    assert flattened[0]["payload_conversation_id"] is None
    assert flattened[0]["identity_verified"] is False
    provenance = json.loads(flattened[0]["provenance_json"])
    assert provenance["chatgpt_payload_identity"] == {
        "requested_conversation_id": "requested-id",
        "payload_conversation_id": None,
        "identity_verified": False,
    }
