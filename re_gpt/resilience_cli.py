"""Operator CLIs for session lifecycle and protocol-drift diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .protocol_drift import build_protocol_fingerprint, compare_protocol_observations
from .session_lifecycle import SessionLifecycle


def _json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def load_observations(path: Path | str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("protocol_observations", "observations"):
            values = raw.get(key)
            if isinstance(values, list):
                return [dict(item) for item in values if isinstance(item, dict)]
        canonical = raw.get("canonical_receipt")
        if isinstance(canonical, dict):
            values = canonical.get("protocol_observations")
            if isinstance(values, list):
                return [dict(item) for item in values if isinstance(item, dict)]
    raise ValueError(f"{path} does not contain protocol observations")


def _session_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="re-gpt-session", description="Inspect and refresh saved ChatGPT session state without exposing secrets.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "bootstrap", "refresh", "doctor"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile-dir", type=Path)
        cmd.add_argument("--snapshot-path", type=Path)
        if name in {"bootstrap", "refresh"}:
            cmd.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
            cmd.add_argument("--cdp-endpoint")
            cmd.add_argument("--timeout", type=int, default=30)
    return parser


def session_main(argv: list[str] | None = None) -> int:
    args = _session_parser().parse_args(argv)
    lifecycle = SessionLifecycle()
    common = {"profile_dir": args.profile_dir, "snapshot_path": args.snapshot_path}
    if args.command == "status":
        result = lifecycle.status(**common)
    elif args.command == "doctor":
        result = lifecycle.doctor(**common)
    else:
        method = lifecycle.bootstrap if args.command == "bootstrap" else lifecycle.refresh
        result = method(
            **common,
            browser_mode=args.browser_mode,
            cdp_endpoint=args.cdp_endpoint,
            timeout=args.timeout,
        )
    _json_print(result)
    state = str(result.get("state") or result.get("session_state") or "unknown")
    return 0 if state in {"ready", "partial"} else 2


def _protocol_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="re-gpt-protocol", description="Build and compare redacted structural protocol fingerprints.")
    sub = parser.add_subparsers(dest="command", required=True)
    fingerprint = sub.add_parser("fingerprint")
    fingerprint.add_argument("observations", type=Path)
    check = sub.add_parser("check")
    check.add_argument("baseline", type=Path)
    check.add_argument("candidate", type=Path)
    return parser


def protocol_main(argv: list[str] | None = None) -> int:
    args = _protocol_parser().parse_args(argv)
    if args.command == "fingerprint":
        result = build_protocol_fingerprint(load_observations(args.observations))
        _json_print(result)
        return 0
    result = compare_protocol_observations(load_observations(args.baseline), load_observations(args.candidate))
    _json_print(result)
    classification = result["classification"]
    return 0 if classification == "compatible" else (1 if classification == "drift_suspected" else 2)


if __name__ == "__main__":
    raise SystemExit(session_main())
