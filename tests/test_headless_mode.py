from __future__ import annotations

from pathlib import Path

import pytest

from re_gpt.accessibility_browser import (
    AccessibilityBrowserRelay,
    build_parser,
    resolve_browser_mode,
)


def test_parser_defaults_to_displayless_ozone() -> None:
    args = build_parser().parse_args(["6a33ae58-cb84-83ec-b187-ddab3179ccbb", "--prompt", "hi"])
    assert args.browser_mode == "displayless"


def test_parser_accepts_unified_headless_and_headed_fallbacks() -> None:
    headless = build_parser().parse_args(
        [
            "6a33ae58-cb84-83ec-b187-ddab3179ccbb",
            "--prompt",
            "hi",
            "--browser-mode",
            "headless",
        ]
    )
    headed = build_parser().parse_args(
        [
            "6a33ae58-cb84-83ec-b187-ddab3179ccbb",
            "--prompt",
            "hi",
            "--browser-mode",
            "headed",
        ]
    )
    assert headless.browser_mode == "headless"
    assert headed.browser_mode == "headed"


def test_legacy_headed_flag_maps_to_headed_mode() -> None:
    args = build_parser().parse_args(
        ["6a33ae58-cb84-83ec-b187-ddab3179ccbb", "--prompt", "hi", "--headed"]
    )
    assert resolve_browser_mode(args) == "headed"


def test_launch_options_use_ozone_displayless_without_chrome_headless(tmp_path: Path) -> None:
    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "profile",
        browser_mode="displayless",
        timeout_ms=1000,
        idle_ms=100,
    )
    options = relay.launch_options()
    assert options["headless"] is False
    assert options["channel"] == "chrome"
    assert "--ozone-platform=headless" in options["args"]
    assert "--ozone-override-screen-size=1440,960" in options["args"]
    assert "--start-minimized" not in options["args"]


def test_launch_options_keep_unified_chromium_headless_as_secondary_mode(tmp_path: Path) -> None:
    relay = AccessibilityBrowserRelay(
        profile_dir=tmp_path / "profile",
        browser_mode="headless",
        timeout_ms=1000,
        idle_ms=100,
    )
    options = relay.launch_options()
    assert options["headless"] is True
    assert options["channel"] == "chromium"
    assert "--ozone-platform=headless" not in options["args"]


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
    assert "--ozone-platform=headless" not in options["args"]


def test_relay_rejects_unknown_browser_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="browser mode"):
        AccessibilityBrowserRelay(
            profile_dir=tmp_path / "profile",
            browser_mode="bogus",
            timeout_ms=1000,
            idle_ms=100,
        )
