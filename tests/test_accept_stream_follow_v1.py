from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location("accept_stream_follow_v1", Path("scripts/accept_stream_follow_v1.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_requires_progress_terminal_and_zero_poll(monkeypatch, tmp_path) -> None:
    module = _load()

    class FakeLimiter:
        def __init__(self, db_path):
            self.db_path = db_path

        def snapshot(self):
            return {"backoff_level": 0, "total_429": 0}

    monkeypatch.setattr(module, "GlobalBackendRateLimiter", FakeLimiter)

    class FakeService:
        def continue_thread(self, conversation_id, prompt, **kwargs):
            callback = kwargs["progress_callback"]
            callback({"kind": "thinking", "content_type": "thoughts"})
            callback({"kind": "token_delta", "delta": "ok"})
            callback({"kind": "terminal", "reason": "done"})
            return {
                "state": "complete",
                "operation_id": "op-1",
                "user_node_id": "u-1",
                "assistant_node_id": "a-1",
                "read_adapter": "async",
                "stream_terminal_observed": True,
                "stream_terminal_reason": "done",
                "stream_event_counts": {"thinking": 1, "token_delta": 1, "terminal": 1},
                "follow_mode": "stream_terminal_single_reconcile",
                "terminal_reconcile_count": 1,
                "reconcile_poll_count": 0,
            }

    result = module.run_acceptance(
        FakeService(),
        conversation_id="conv-1",
        nonce="N",
        db_path=tmp_path / "state.db",
    )
    assert result["all_ok"] is True
    assert result["checks"]["zero_poll"] is True
    assert result["checks"]["single_terminal_reconcile"] is True
    assert [event["kind"] for event in result["progress"]] == ["thinking", "token_delta", "terminal"]
