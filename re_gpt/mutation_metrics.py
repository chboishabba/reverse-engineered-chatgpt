"""Stage timing summaries for durable mutation attempts."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .operation_store import MutationOperationStore
from .storage import DEFAULT_DB_PATH


STAGES = (
    ("start_to_auth", "started_at", "authenticated_at"),
    ("auth_to_hydrated", "authenticated_at", "hydrated_at"),
    ("hydrated_to_submit", "hydrated_at", "submitted_at"),
    ("submit_to_commit", "submitted_at", "committed_at"),
    ("commit_to_complete", "committed_at", "completed_at"),
)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("percentile of empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_ci(
    values: list[float],
    statistic,
    *,
    samples: int = 1000,
    seed: int = 0,
) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(max(100, samples)):
        draw = [values[rng.randrange(len(values))] for _ in values]
        estimates.append(float(statistic(draw)))
    return (_percentile(estimates, 0.025), _percentile(estimates, 0.975))


def summarize_values(values: Iterable[float]) -> dict[str, Any]:
    sample = [float(value) for value in values if value is not None and float(value) >= 0]
    if not sample:
        return {"n": 0, "p50_s": None, "p95_s": None, "p50_ci95_s": None, "p95_ci95_s": None}
    p50 = _percentile(sample, 0.5)
    p95 = _percentile(sample, 0.95)
    return {
        "n": len(sample),
        "p50_s": p50,
        "p95_s": p95,
        "p50_ci95_s": _bootstrap_ci(sample, lambda xs: _percentile(xs, 0.5), seed=50),
        "p95_ci95_s": _bootstrap_ci(sample, lambda xs: _percentile(xs, 0.95), seed=95),
    }


def timing_report(db_path: Path | str = DEFAULT_DB_PATH, *, limit: int = 500) -> dict[str, Any]:
    with MutationOperationStore(db_path) as store:
        rows = store.recent(limit=limit)

    stage_values: dict[str, list[float]] = {name: [] for name, _, _ in STAGES}
    for row in rows:
        for name, start_key, end_key in STAGES:
            start = row.get(start_key)
            end = row.get(end_key)
            if start is None or end is None:
                continue
            try:
                duration = float(end) - float(start)
            except (TypeError, ValueError):
                continue
            if duration >= 0:
                stage_values[name].append(duration)

    return {
        "attempts_considered": len(rows),
        "stages": {name: summarize_values(values) for name, values in stage_values.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize re-gpt mutation stage timings")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(timing_report(args.db, limit=args.limit), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
