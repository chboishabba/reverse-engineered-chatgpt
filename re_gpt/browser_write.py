"""Synchronous adapter for the production browser-owned mutation transport."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class BrowserWriteError(RuntimeError):
    """Raised when the browser-owned mutation command fails."""


def submit_prompt(
    selector: str,
    prompt: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
    follow: bool = False,
) -> dict[str, Any]:
    """Submit *prompt* through the authenticated real ChatGPT composer.

    Production writes are DOM-only.  The historical handcrafted same-origin API
    path remains available through ``re_gpt.accessibility_browser --transport
    api`` for diagnostics, but normal mutation never waits on or falls back from
    that protocol.  By default this function returns after browser submission
    evidence; ``follow=True`` waits for a settled assistant response.
    """

    selector = selector.strip()
    prompt = prompt.strip()
    if not selector:
        raise ValueError("conversation selector is empty")
    if not prompt:
        raise ValueError("prompt is empty")

    command = [
        sys.executable,
        "-m",
        "re_gpt.mutation_browser",
        selector,
        "--browser-mode",
        browser_mode,
        "--timeout",
        str(timeout),
        "--json",
    ]
    if follow:
        command.append("--follow")
    if cdp_endpoint:
        command.extend(["--cdp-endpoint", cdp_endpoint])
    elif profile_dir is not None:
        command.extend(["--profile-dir", str(profile_dir)])

    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown browser failure").strip()
        raise BrowserWriteError(detail)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserWriteError(
            f"browser relay returned invalid JSON: {completed.stdout[:500]!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise BrowserWriteError("browser relay returned a non-object JSON payload")
    return payload
