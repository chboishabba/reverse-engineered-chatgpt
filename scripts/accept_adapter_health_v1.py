#!/usr/bin/env python3
"""Acceptance runner for read-adapter health/backoff and latency telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time
from typing import Any

from re_gpt.adapter_health import AdapterHealthStore
from re_gpt.graph_broker import fetch_exact_graph
from re_gpt.storage import DEFAULT_DB_PATH


def _synthetic_backoff_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="re-gpt-adapter-health-") as raw:
        db = Path(raw) / "health.sqlite"
        clock = [100.0]
        with AdapterHealthStore(db, now=lambda: clock[0]) as store:
            store.record_attempt(
                "async",
                elapsed_ms=10.0,
                status=429,
                response_class="RequestContextRejected",
                rejected=True,
                success=False,
            )
            order, skipped = store.rank_adapters(("async", "sync", "browser"))
            snapshot = store.snapshot()
        return {
            "ok": order == ("sync", "browser") and skipped == ("async",),
            "order": list(order),
            "skipped": list(skipped),
            "async": snapshot.get("async", {}),
        }


def run_acceptance(
    conversation_id: str,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    samples: int = 4,
    delay: float = 1.0,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
) -> dict[str, Any]:
    synthetic = _synthetic_backoff_check()
    reads: list[dict[str, Any]] = []
    for index in range(max(1, int(samples))):
        started = time.time()
        result = fetch_exact_graph(
            conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            health_db_path=db_path,
        )
        reads.append({
            "index": index + 1,
            "adapter": result.adapter,
            "elapsed_ms": round(result.elapsed_ms, 3),
            "attempted_adapters": list(result.attempted_adapters),
            "rejected_adapters": list(result.rejected_adapters),
            "health_skipped_adapters": list(result.health_skipped_adapters),
            "wall_elapsed_s": round(time.time() - started, 3),
            "no_duplicate_attempts": len(result.attempted_adapters) == len(set(result.attempted_adapters)),
        })
        if delay > 0 and index + 1 < samples:
            time.sleep(delay)

    with AdapterHealthStore(db_path) as store:
        report = store.report(sample_limit=max(20, samples * 4))

    live_ok = bool(reads) and all(item["no_duplicate_attempts"] for item in reads)
    sampled_adapters = {item["adapter"] for item in reads}
    telemetry_ok = all(
        name in report and int(report[name].get("samples") or 0) >= 1
        for name in sampled_adapters
    )
    return {
        "conversation_id": conversation_id,
        "synthetic_backoff": synthetic,
        "reads": reads,
        "adapter_report": report,
        "all_complete": bool(synthetic.get("ok") and live_ok and telemetry_ok),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify adapter backoff and empirical exact-read telemetry")
    parser.add_argument("conversation_id")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", choices=("displayless", "headless", "headed"), default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_acceptance(
        args.conversation_id,
        db_path=args.db_path,
        samples=args.samples,
        delay=args.delay,
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        browser_timeout=args.browser_timeout,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result.get("all_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
