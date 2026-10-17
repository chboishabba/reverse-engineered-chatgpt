"""Operator CLI for read-adapter health/backoff and latency telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapter_health import AdapterHealthStore
from .storage import DEFAULT_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect re-gpt exact-read adapter health and empirical latency")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--sample-limit", type=int, default=200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with AdapterHealthStore(args.db) as store:
        payload = store.report(sample_limit=args.sample_limit)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
