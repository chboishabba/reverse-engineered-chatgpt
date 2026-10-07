"""Synchronous adapter for the accessibility browser-owned write transport."""

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
    timeout: int = 900,
) -> dict[str, Any]:
    """Submit *prompt* through the accessibility browser transport.

    The child command owns browser lifecycle and performs API-first/DOM-fallback
    mutation.  This adapter exists so the normal ``re-gpt`` CLI can use that
    transport without duplicating browser code.
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
        "re_gpt.accessibility_browser",
        selector,
        "--transport",
        "auto",
        "--browser-mode",
        browser_mode,
        "--timeout",
        str(timeout),
        "--json",
    ]
    if profile_dir is not None:
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
