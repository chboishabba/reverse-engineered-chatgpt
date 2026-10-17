"""Synchronous browser-owned read and DOM-observation adapters.

Structured browser reads are a reconciliation fallback only. DOM status is
provisional observation and is never treated as canonical transcript truth.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chatgpt_dom_relay import DEFAULT_PROFILE_DIR


class BrowserObservationError(RuntimeError):
    """Raised when a browser read/observation command fails or is malformed."""


@dataclass(frozen=True)
class BrowserReadResult:
    chat: dict[str, Any]
    diagnostic: dict[str, Any]


def classify_dom_status(observation: dict[str, Any]) -> dict[str, Any]:
    """Attach one conservative status label to a raw DOM observation."""

    result = dict(observation)
    if result.get("conversation_load_error"):
        status = "conversation_load_error"
    elif result.get("history_load_error"):
        status = "history_load_error"
    elif result.get("connection_interrupted"):
        status = "connection_interrupted"
    elif result.get("tool_activity"):
        status = "tool_activity"
    elif result.get("thinking"):
        status = "thinking"
    elif result.get("generating"):
        status = "streaming"
    elif result.get("loaded") and result.get("assistant_text_visible"):
        status = "settled"
    elif result.get("loaded"):
        status = "ready"
    else:
        status = "hydrating"
    result["status"] = status
    return result


def _run_observer(
    conversation_id: str,
    mode: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "re_gpt.browser_observer_runner",
        conversation_id,
        f"--{mode}",
        "--browser-mode",
        browser_mode,
        "--timeout",
        str(timeout),
        "--json",
    ]
    if cdp_endpoint:
        command.extend(["--cdp-endpoint", cdp_endpoint])
    else:
        command.extend(["--profile-dir", str(Path(profile_dir or DEFAULT_PROFILE_DIR).expanduser())])

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(5, int(timeout) + 10),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserObservationError(f"browser observer launch failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown browser observer failure").strip()
        raise BrowserObservationError(detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserObservationError(
            f"browser observer returned invalid JSON: {completed.stdout[:500]!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise BrowserObservationError("browser observer returned a non-object JSON payload")
    return payload


def fetch_conversation_same_origin(
    conversation_id: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
) -> BrowserReadResult:
    """Fetch one exact conversation through authenticated browser same-origin GET."""

    payload = _run_observer(
        conversation_id,
        "read",
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=timeout,
    )
    chat = payload.get("chat")
    if not isinstance(chat, dict):
        raise BrowserObservationError("browser conversation response is not a JSON object")
    mapping = chat.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise BrowserObservationError("browser conversation response has no non-empty mapping")
    diagnostic = payload.get("diagnostic")
    return BrowserReadResult(
        chat=chat,
        diagnostic=diagnostic if isinstance(diagnostic, dict) else {},
    )


def observe_live_status(
    conversation_id: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Return provisional rendered-state observation for one thread."""

    payload = _run_observer(
        conversation_id,
        "status",
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=timeout,
    )
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        raise BrowserObservationError("browser status response has no observation object")
    return classify_dom_status(observation)
