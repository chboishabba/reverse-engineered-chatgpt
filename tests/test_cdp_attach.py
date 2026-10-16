from __future__ import annotations

import json
import subprocess
from pathlib import Path

from re_gpt.accessibility_browser import AccessibilityBrowserRelay, build_parser
from re_gpt.browser_write import submit_prompt


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_accessibility_parser_accepts_cdp_endpoint() -> None:
    args = build_parser().parse_args(
        [
            "6acc564d-0314-83ec-8036-1f4d9868c5f3",
            "--prompt",
            "hello",
            "--cdp-endpoint",
            "http://127.0.0.1:9222",
        ]
    )
    assert args.cdp_endpoint == "http://127.0.0.1:9222"


def test_relay_marks_cdp_as_attached_browser(tmp_path: Path) -> None:
    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "unused-profile",
        browser_mode="displayless",
        cdp_endpoint="http://127.0.0.1:9222",
        timeout_ms=1000,
        idle_ms=100,
    )
    assert relay.cdp_endpoint == "http://127.0.0.1:9222"
    assert relay.attaches_existing_browser is True


def test_submit_prompt_forwards_cdp_endpoint(monkeypatch) -> None:
    observed = []

    def fake_run(command, *, input, text, capture_output, check):
        observed.append(command)
        return _Completed(
            0,
            json.dumps(
                {
                    "latest_assistant": "ACK",
                    "resolved_thread_id": "abc",
                    "transport": "api",
                    "browser_mode": "attached",
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = submit_prompt(
        "abc",
        "hello",
        cdp_endpoint="http://127.0.0.1:9222",
    )

    command = observed[0]
    assert command[command.index("--cdp-endpoint") + 1] == "http://127.0.0.1:9222"
    assert result["latest_assistant"] == "ACK"
