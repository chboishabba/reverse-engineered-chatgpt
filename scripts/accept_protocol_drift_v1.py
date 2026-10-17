#!/usr/bin/env python3
"""Acceptance runner for redacted ProtocolDrift compatibility receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from re_gpt.protocol_drift import build_protocol_fingerprint, compare_protocol_fingerprints
from re_gpt.resilience_cli import load_observations


def _self_checks(fp: dict[str, Any]) -> dict[str, Any]:
    exact = compare_protocol_fingerprints(fp, fp)

    additive = dict(fp)
    additive["header_names"] = sorted(set(fp.get("header_names") or []) | {"x-re-gpt-acceptance-added"})
    additive_result = compare_protocol_fingerprints(fp, additive)

    breaking = dict(fp)
    routes = list(fp.get("routes") or [])
    if routes:
        breaking["routes"] = routes[:-1]
    else:
        breaking["body_keys"] = []
    breaking_result = compare_protocol_fingerprints(fp, breaking)

    return {
        "exact": exact["classification"],
        "additive": additive_result["classification"],
        "breaking": breaking_result["classification"],
        "passed": (
            exact["classification"] == "compatible"
            and additive_result["classification"] == "drift_suspected"
            and breaking_result["classification"] == "incompatible"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--allow-drift-suspected", action="store_true")
    args = parser.parse_args()

    baseline = build_protocol_fingerprint(load_observations(args.baseline))
    candidate = build_protocol_fingerprint(load_observations(args.candidate))
    comparison = compare_protocol_fingerprints(baseline, candidate)
    receipt = {
        "schema_version": 1,
        "comparison": comparison,
        "baseline": baseline,
        "candidate": candidate,
        "self_checks": _self_checks(baseline),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))

    acceptable = comparison["classification"] == "compatible" or (
        args.allow_drift_suspected and comparison["classification"] == "drift_suspected"
    )
    return 0 if acceptable and receipt["self_checks"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
