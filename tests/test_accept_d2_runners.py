from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_acceptance_synthetic_backoff_self_check() -> None:
    module = _load("accept_adapter_health_v1", "scripts/accept_adapter_health_v1.py")
    result = module._synthetic_backoff_check()
    assert result["ok"] is True
    assert result["order"] == ["sync", "browser"]
    assert result["skipped"] == ["async"]


def test_attachment_acceptance_builds_all_four_cases_without_network(tmp_path) -> None:
    module = _load("accept_attachment_matrix_v1", "scripts/accept_attachment_matrix_v1.py")

    class FakeService:
        def __init__(self):
            self.calls = []

        def new_conversation(self, prompt, **kwargs):
            return {"state": "complete", "conversation_id": "conv-1"}

        def continue_thread(self, conversation_id, prompt, *, attachments, **kwargs):
            self.calls.append((prompt, [Path(p) for p in attachments]))
            return {
                "state": "complete",
                "conversation_id": conversation_id,
                "operation_id": f"op-{len(self.calls)}",
                "user_node_id": f"u-{len(self.calls)}",
                "assistant_node_id": f"a-{len(self.calls)}",
                "transport": "async",
                "read_adapter": "async",
                "attachment_ids": [f"file-{len(self.calls)}-{i}" for i in range(len(attachments))],
            }

    service = FakeService()
    result = module.run_acceptance(
        service,
        nonce="N",
        work_dir=tmp_path,
        large_bytes=4096,
        step_delay=0,
    )
    assert result["all_complete"] is True
    assert len(service.calls) == 4
    assert [len(call[1]) for call in service.calls] == [2, 2, 1, 1]
    duplicate_names = [path.name for path in service.calls[1][1]]
    assert duplicate_names == ["same.txt", "same.txt"]
    assert service.calls[2][1][0].name == "large.txt"
    assert service.calls[2][1][0].stat().st_size >= 4096
    assert service.calls[3][1][0].suffix == ".png"
