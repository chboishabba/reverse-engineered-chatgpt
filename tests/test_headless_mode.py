from __future__ import annotations

from pathlib import Path

import pytest

from re_gpt.accessibility_browser import (
    AccessibilityBrowserRelay,
    build_parser,
    resolve_browser_mode,
)


def test_parser_defaults_to_unified_headless() -> None:
    args = build_parser().parse_args(["6a33ae58-cb84-83ec-b187-ddab3179ccbb", "--prompt", "hi"])
    assert args.browser_mode == "headless"


def test_parser_accepts_headed_fallback() -> None:
    args = build_parser().parse_args(
        [
            "6a33ae58-cb84-83ec-b187-ddab3179ccbb",
            "--prompt",
            "hi",
            "--browser-mode",
            "headed",
        ]
    )
    assert args.browser_mode == "headed"


def test_legacy_headed_flag_maps_to_headed_mode() -> None:
    args = build_parser().parse_args(
        ["6a33ae58-cb84-83ec-b187-ddab3179ccbb", "--prompt", "hi", "--headed"]
    )
    assert resolve_browser_mode(args) == "headed"


def test_launch_options_use_playwright_chromium_for_unified_headless(tmp_path: Path) -> None:
    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "profile",
        browser_mode="headless",
        timeout_ms=1000,
        idle_ms=100,
    )
    options = relay.launch_options()
    assert options["headless"] is True
    assert options["channel"] == "chromium"
    assert "--start-minimized" not in options["args"]


def test_launch_options_use_system_chrome_for_headed_fallback(tmp_path: Path) -> None:
    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "profile",
        browser_mode="headed",
        timeout_ms=1000,
        idle_ms=100,
    )
    options = relay.launch_options()
    assert options["headless"] is False
    assert options["channel"] == "chrome"
    assert "--start-minimized" in options["args"]


def test_relay_rejects_unknown_browser_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="browser mode"):
        AccessibilityBrowserRelay(
            profile_dir=tmp_path / "profile",
            browser_mode="bogus",
            timeout_ms=1000,
            idle_ms=100,
        )
