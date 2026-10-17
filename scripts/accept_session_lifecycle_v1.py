#!/usr/bin/env python3
"""Live operator acceptance for Milestone D1 SessionLifecycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from re_gpt.session_lifecycle import SessionLifecycle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--snapshot-path", type=Path)
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--step-delay", type=float, default=2.0)
    args = parser.parse_args()

    lifecycle = SessionLifecycle()
    common = {"profile_dir": args.profile_dir, "snapshot_path": args.snapshot_path}
    capture = {
        **common,
        "browser_mode": args.browser_mode,
        "cdp_endpoint": args.cdp_endpoint,
        "timeout": args.timeout,
    }

    receipt: dict[str, object] = {"schema_version": 1, "steps": {}}
    receipt["steps"]["status_before"] = lifecycle.status(**common)  # type: ignore[index]
    receipt["steps"]["bootstrap"] = lifecycle.bootstrap(**capture)  # type: ignore[index]
    time.sleep(max(0.0, args.step_delay))
    receipt["steps"]["doctor_after_bootstrap"] = lifecycle.doctor(**common)  # type: ignore[index]
    receipt["steps"]["refresh"] = lifecycle.refresh(**capture)  # type: ignore[index]
    receipt["steps"]["doctor_after_refresh"] = lifecycle.doctor(**common)  # type: ignore[index]

    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    print(rendered)
    final = receipt["steps"]["doctor_after_refresh"]  # type: ignore[index]
    ready = isinstance(final, dict) and final.get("session_state") == "ready"
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
