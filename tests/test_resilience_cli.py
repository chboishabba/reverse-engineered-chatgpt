from __future__ import annotations

import json
from pathlib import Path

from re_gpt import resilience_cli


def test_extract_observations_accepts_direct_list_and_receipt_wrapper(tmp_path: Path) -> None:
    obs = [{"method": "GET", "path": "/api/auth/session"}]
    direct = tmp_path / "direct.json"
    wrapped = tmp_path / "wrapped.json"
    direct.write_text(json.dumps(obs), encoding="utf-8")
    wrapped.write_text(json.dumps({"protocol_observations": obs}), encoding="utf-8")
    assert resilience_cli.load_observations(direct) == obs
    assert resilience_cli.load_observations(wrapped) == obs


def test_protocol_check_exit_codes(monkeypatch, tmp_path: Path, capsys) -> None:
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    base.write_text(json.dumps([{"method":"POST","path":"/backend-api/f/conversation","bootstrap_stage":"conversation_post","body_keys":["action","messages"]}]), encoding="utf-8")
    cand.write_text(json.dumps([{"method":"POST","path":"/backend-api/f/conversation","bootstrap_stage":"conversation_post","body_keys":["action"]}]), encoding="utf-8")
    code = resilience_cli.protocol_main(["check", str(base), str(cand)])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["classification"] == "incompatible"


def test_session_status_emits_json(monkeypatch, capsys) -> None:
    class FakeLifecycle:
        def status(self, **kwargs):
            return {"state": "ready", "access_token": {"state": "fresh"}}
    monkeypatch.setattr(resilience_cli, "SessionLifecycle", lambda: FakeLifecycle())
    code = resilience_cli.session_main(["status"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"
