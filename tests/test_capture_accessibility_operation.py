from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.capture_accessibility_operation as cli


def _receipt() -> dict:
    return {
        "schema_version": 1,
        "operation": "attachment_continue",
        "actuator": "browser_dom",
        "conversation_id": "conversation-1",
        "actuator_attempts": 1,
        "local_inputs": {
            "attachments": [
                {
                    "filename": "probe.txt",
                    "mime_type": "text/plain",
                    "size": 6,
                    "sha256": "abc123",
                }
            ]
        },
        "canonical_receipt": {"state": "complete"},
    }


def test_parser_requires_attachment_and_prompt_source(tmp_path: Path) -> None:
    parser = cli.build_parser()
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")

    args = parser.parse_args(
        [
            "attachment-continue",
            "conversation-1",
            "--prompt",
            "ACCESSIBILITY-PROBE",
            "--attach",
            str(attachment),
            "--browser-mode",
            "displayless",
            "--timeout",
            "30",
        ]
    )
    assert args.operation == "attachment-continue"
    assert args.selector == "conversation-1"
    assert args.attach == [attachment]
    assert args.timeout == 30

    with pytest.raises(SystemExit):
        parser.parse_args(["attachment-continue", "conversation-1", "--prompt", "x"])


def test_main_writes_share_safe_json_to_output(tmp_path: Path, monkeypatch) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    output = tmp_path / "capture.json"
    seen = {}

    def fake_capture(selector, prompt, attachments, **kwargs):
        seen.update({"selector": selector, "prompt": prompt, "attachments": list(attachments), **kwargs})
        return _receipt()

    monkeypatch.setattr(cli, "capture_attachment_continue", fake_capture)
    rc = cli.main(
        [
            "attachment-continue",
            "conversation-1",
            "--prompt",
            "ACCESSIBILITY-PROBE",
            "--attach",
            str(attachment),
            "--output",
            str(output),
            "--db-path",
            str(tmp_path / "capture.db"),
        ]
    )

    assert rc == 0
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["canonical_receipt"]["state"] == "complete"
    assert seen["selector"] == "conversation-1"
    rendered = output.read_text(encoding="utf-8")
    assert str(attachment.resolve()) not in rendered


def test_main_prints_json_to_stdout_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(cli, "capture_attachment_continue", lambda *args, **kwargs: _receipt())

    rc = cli.main(
        [
            "attachment-continue",
            "conversation-1",
            "--prompt",
            "ACCESSIBILITY-PROBE",
            "--attach",
            str(attachment),
        ]
    )

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema_version"] == 1


def test_prompt_file_is_supported(tmp_path: Path, monkeypatch) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("ACCESSIBILITY-FILE-PROBE\n", encoding="utf-8")
    seen = {}

    def fake_capture(selector, prompt, attachments, **kwargs):
        seen["prompt"] = prompt
        return _receipt()

    monkeypatch.setattr(cli, "capture_attachment_continue", fake_capture)
    rc = cli.main(
        [
            "attachment-continue",
            "conversation-1",
            "--prompt-file",
            str(prompt_file),
            "--attach",
            str(attachment),
        ]
    )
    assert rc == 0
    assert seen["prompt"] == "ACCESSIBILITY-FILE-PROBE"


def test_post_capture_output_failure_falls_back_to_stdout_without_rerun(tmp_path: Path, monkeypatch, capsys) -> None:
    attachment = tmp_path / "probe.txt"
    attachment.write_text("hello\n", encoding="utf-8")
    output = tmp_path / "capture.json"
    calls = {"capture": 0}

    def fake_capture(*args, **kwargs):
        calls["capture"] += 1
        return _receipt()

    monkeypatch.setattr(cli, "capture_attachment_continue", fake_capture)
    original_write_text = Path.write_text

    def fail_only_output(self: Path, data: str, *args, **kwargs):
        if self == output:
            raise OSError("disk write failed")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_only_output)
    rc = cli.main(
        [
            "attachment-continue",
            "conversation-1",
            "--prompt",
            "ACCESSIBILITY-PROBE",
            "--attach",
            str(attachment),
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert calls["capture"] == 1
    assert json.loads(captured.out)["canonical_receipt"]["state"] == "complete"
    assert "do not rerun" in captured.err.lower()
