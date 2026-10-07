"""Browser-backed ChatGPT write transport.

The read/archive plane remains in :mod:`re_gpt.sync_chatgpt`.  Mutating an
existing ChatGPT thread is delegated to ``chatgpt_dom_relay.py`` so the browser
owns authentication, Cloudflare state, sentinel requirements, and request
affinity.  Same-origin browser fetch is preferred; DOM composer submission is a
fail-closed compatibility fallback.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class BrowserWriteError(RuntimeError):
    """Raised when every configured browser write transport fails."""


def default_relay_path() -> Path:
    """Return the repository relay path used by editable/source installs."""

    return Path(__file__).resolve().parent.parent / "chatgpt_dom_relay.py"


def _relay_command(
    selector: str,
    *,
    transport: str,
    relay_path: Path,
    profile_dir: Path | None,
    headed: bool,
    timeout: int,
) -> list[str]:
    command = [
        sys.executable,
        str(relay_path),
        selector,
        "--transport",
        transport,
        "--json",
        "--timeout",
        str(timeout),
    ]
    if profile_dir is not None:
        command.extend(["--profile-dir", str(profile_dir)])
    if headed:
        command.append("--headed")
    return command


def _run_transport(
    selector: str,
    prompt: str,
    *,
    transport: str,
    relay_path: Path,
    profile_dir: Path | None,
    headed: bool,
    timeout: int,
) -> dict[str, Any]:
    if not relay_path.exists():
        raise BrowserWriteError(
            f"Browser relay not found at {relay_path}. Run re-gpt from a source/editable "
            "checkout that includes chatgpt_dom_relay.py."
        )

    completed = subprocess.run(
        _relay_command(
            selector,
            transport=transport,
            relay_path=relay_path,
            profile_dir=profile_dir,
            headed=headed,
            timeout=timeout,
        ),
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown relay failure").strip()
        raise BrowserWriteError(detail)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserWriteError(
            f"{transport} relay returned invalid JSON: {completed.stdout[:500]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise BrowserWriteError(f"{transport} relay returned a non-object JSON payload")
    payload.setdefault("transport", transport)
    return payload


def submit_prompt(
    selector: str,
    prompt: str,
    *,
    relay_path: Path | str | None = None,
    profile_dir: Path | str | None = None,
    headed: bool = False,
    timeout: int = 900,
) -> dict[str, Any]:
    """Submit *prompt* to an existing ChatGPT thread.

    Browser-context same-origin API fetch is attempted first.  If that write
    path fails (for example because ChatGPT changed a sentinel requirement), the
    same authenticated browser profile is retried through the rendered composer
    DOM.  Both paths are browser-owned; copied-cookie Python POST is deliberately
    not part of this production fallback chain.
    """

    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is empty")
    selector = selector.strip()
    if not selector:
        raise ValueError("conversation selector is empty")

    resolved_relay = Path(relay_path) if relay_path is not None else default_relay_path()
    resolved_profile = Path(profile_dir) if profile_dir is not None else None

    failures: list[tuple[str, str]] = []
    for transport in ("api", "dom"):
        try:
            payload = _run_transport(
                selector,
                prompt,
                transport=transport,
                relay_path=resolved_relay,
                profile_dir=resolved_profile,
                headed=headed,
                timeout=timeout,
            )
        except BrowserWriteError as exc:
            failures.append((transport, str(exc)))
            continue

        payload["transport"] = transport
        if failures:
            payload["fallback_from"] = failures[-1][0]
        return payload

    details = "; ".join(f"{transport}: {detail}" for transport, detail in failures)
    raise BrowserWriteError(f"all browser write transports failed ({details})")
