from __future__ import annotations

import json

from re_gpt.adapter_health import AdapterHealthStore
from re_gpt import adapter_health_cli
from re_gpt import mcp_server


def test_adapter_health_cli_emits_json(tmp_path, capsys) -> None:
    db = tmp_path / "archive.sqlite"
    with AdapterHealthStore(db) as store:
        store.record_attempt("async", elapsed_ms=12.5, status=200, response_class="ok", success=True)
    code = adapter_health_cli.main(["--db", str(db), "--sample-limit", "10"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["async"]["samples"] == 1
    assert payload["async"]["p50_ms"] == 12.5


def test_mcp_adapter_health_reads_requested_database(tmp_path) -> None:
    db = tmp_path / "archive.sqlite"
    with AdapterHealthStore(db) as store:
        store.record_attempt("sync", elapsed_ms=44.0, status=200, response_class="ok", success=True)
    payload = mcp_server.chatgpt_adapter_health(str(db), sample_limit=10)
    assert payload["sync"]["success_count"] == 1
    assert payload["sync"]["samples"] == 1
