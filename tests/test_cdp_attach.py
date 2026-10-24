from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from re_gpt.accessibility_browser import (
    AccessibilityBrowserRelay,
    _is_authenticated_chatgpt_url,
    _is_chatgpt_url,
    build_parser,
)
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


def test_attached_api_context_reuses_existing_chatgpt_page(tmp_path: Path) -> None:
    class Page:
        url = "https://chatgpt.com/c/6adce5a3-ccd8-83ec-b87b-6264af35ece7"

        async def goto(self, *args, **kwargs):
            raise AssertionError("attached API context must not navigate the operator's page")

        async def title(self):
            return "Clarify Request"

    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "unused-profile",
        browser_mode="displayless",
        cdp_endpoint="http://127.0.0.1:9222",
        timeout_ms=1000,
        idle_ms=100,
    )
    relay._page = Page()

    asyncio.run(relay.open_api_context())


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


def test_is_chatgpt_url_rejects_non_https_and_lookalikes() -> None:
    assert _is_chatgpt_url("https://chatgpt.com/c/123") is True
    assert _is_chatgpt_url("http://chatgpt.com/c/123") is False
    assert _is_chatgpt_url("https://chatgpt.com.attacker.com/c/123") is False
    assert _is_chatgpt_url("https://attacker.com/chatgpt.com") is False
    assert _is_chatgpt_url("https://sub.chatgpt.com/c/123") is False
    assert _is_chatgpt_url("") is False
    assert _is_chatgpt_url(None) is False


def test_is_authenticated_chatgpt_url_filters_auth_routes() -> None:
    assert _is_authenticated_chatgpt_url("https://chatgpt.com/c/123") is True
    assert _is_authenticated_chatgpt_url("https://chatgpt.com/auth/login") is False
    assert _is_authenticated_chatgpt_url("https://chatgpt.com/login") is False
    assert _is_authenticated_chatgpt_url("https://attacker.com/c/123") is False


def test_attached_api_context_rejects_lookalike_and_unauthenticated(tmp_path: Path) -> None:
    class Page:
        def __init__(self, url: str) -> None:
            self.url = url

        async def goto(self, *args, **kwargs):
            raise AssertionError("must not navigate")

        async def title(self):
            return "ChatGPT"

    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "unused-profile",
        browser_mode="displayless",
        cdp_endpoint="http://127.0.0.1:9222",
        timeout_ms=1000,
        idle_ms=100,
    )

    relay._page = Page("https://chatgpt.com.evil.com/c/123")
    with pytest.raises(RuntimeError, match="Attached browser has no ChatGPT page"):
        asyncio.run(relay.open_api_context())

    relay._page = Page("https://chatgpt.com/auth/login")
    with pytest.raises(RuntimeError, match="Attached browser is not authenticated"):
        asyncio.run(relay.open_api_context())

