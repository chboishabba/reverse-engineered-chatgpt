#!/usr/bin/env python3
"""Opt-in live acceptance matrix for existing-thread re-gpt + MCP v1.

The runner first performs non-mutating exact-read broker preflights. Real browser
mutations remain explicit and the already-proven short case is skipped by
default.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from re_gpt.graph_broker import GraphAcquisitionFailed, fetch_exact_graph
from re_gpt.mutation_service import MutationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight and run live existing-thread ACK acceptance cases"
    )
    parser.add_argument("--short-thread", required=True)
    parser.add_argument("--medium-thread", required=True)
    parser.add_argument("--long-thread", required=True)
    parser.add_argument("--attachment", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--browser-mode", default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--browser-timeout", type=int, default=60)
    parser.add_argument("--follow-timeout", type=float, default=120.0)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument(
        "--include-short-mutation",
        action="store_true",
        help="Repeat the short mutation case. Off by default because the short case is already proven.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run exact-read broker preflights only; perform no mutations.",
    )
    parser.add_argument(
        "--probe-async-write",
        action="store_true",
        help="Opt-in: run one sacrificial async-CFFI HTTP write probe on --short-thread and certify via canonical graph.",
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=22.0,
        help="Seconds to pause between sequential mutation probes to stay under per-minute rate limits.",
    )
    return parser


def _preflight(
    label: str,
    conversation_id: str,
    *,
    profile_dir: Path | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fetch_exact_graph(
            conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )
    except GraphAcquisitionFailed as exc:
        return {
            "label": label,
            "conversation_id": conversation_id,
            "accepted": False,
            "adapter": None,
            "attempted_adapters": list(exc.attempted_adapters),
            "rejected_adapters": list(exc.rejected_adapters),
            "observations": list(exc.observations),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error_class": exc.__class__.__name__,
        }
    return {
        "label": label,
        "conversation_id": conversation_id,
        "accepted": True,
        "adapter": result.adapter,
        "attempted_adapters": list(result.attempted_adapters),
        "rejected_adapters": list(result.rejected_adapters),
        "observations": list(result.observations),
        "mapping_nodes": len(result.graph.get("mapping") or {}),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "error_class": None,
    }


def _probe(
    service: MutationService,
    label: str,
    conversation_id: str,
    *,
    attachment: Path | None,
    profile_dir: Path | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
    follow_timeout: float,
    write_transport: str = "dom",
) -> dict[str, Any]:
    nonce = f"REGPT-V1-{label.upper()}-{time.time_ns()}"
    prompt = f"Reply exactly {nonce}."
    started = time.perf_counter()
    try:
        result = service.continue_thread(
            conversation_id,
            prompt,
            attachments=[attachment] if attachment is not None else (),
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            write_transport=write_transport,
            follow=True,
            follow_timeout=follow_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "conversation_id": conversation_id,
            "write_transport": write_transport,
            "operation_id": None,
            "state": "failed_precommit",
            "read_adapter": None,
            "attempted_read_adapters": [],
            "rejected_read_adapters": [],
            "read_observations": [],
            "write_observations": [
                obs.to_dict() for obs in getattr(exc, "observations", ())
            ],
            "user_node_id": None,
            "assistant_node_id": None,
            "assistant_text": "",
            "expected_reply": f"{nonce}.",
            "attachment_ids": [],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "accepted": False,
            "error_class": exc.__class__.__name__,
            "error_detail": str(exc),
        }

    elapsed = round(time.perf_counter() - started, 3)
    assistant_text = str(result.get("assistant_text") or "").strip()
    expected_reply = f"{nonce}."
    attachment_ids = list(result.get("attachment_ids") or [])
    accepted = (
        result.get("state") == "complete"
        and assistant_text in {expected_reply, nonce}
        and (attachment is None or bool(attachment_ids))
    )
    return {
        "label": label,
        "conversation_id": conversation_id,
        "write_transport": write_transport,
        "operation_id": result.get("operation_id"),
        "state": result.get("state"),
        "read_adapter": result.get("read_adapter"),
        "attempted_read_adapters": result.get("attempted_read_adapters", []),
        "rejected_read_adapters": result.get("rejected_read_adapters", []),
        "read_observations": result.get("read_observations", []),
        "write_observations": result.get("write_observations", []),
        "user_node_id": result.get("user_node_id"),
        "assistant_node_id": result.get("assistant_node_id"),
        "assistant_text": assistant_text,
        "expected_reply": expected_reply,
        "attachment_ids": attachment_ids,
        "elapsed_seconds": elapsed,
        "accepted": accepted,
        "error_class": result.get("error_class"),
        "error_detail": result.get("error_detail"),
    }


def main() -> int:
    args = build_parser().parse_args()
    attachment = args.attachment.expanduser().resolve()
    if not attachment.is_file():
        raise SystemExit(f"attachment does not exist or is not a file: {attachment}")

    common_read = {
        "profile_dir": args.profile_dir,
        "browser_mode": args.browser_mode,
        "cdp_endpoint": args.cdp_endpoint,
        "browser_timeout": args.browser_timeout,
    }
    step_delay = max(0.0, float(args.step_delay))
    preflight_delay = min(16.0, step_delay)
    preflights = []
    for idx, (label, thread_id) in enumerate(
        (
            ("short", args.short_thread),
            ("medium", args.medium_thread),
            ("long", args.long_thread),
        )
    ):
        if idx > 0 and preflight_delay > 0:
            time.sleep(preflight_delay)
        item = _preflight(label, thread_id, **common_read)
        preflights.append(item)
        if not item["accepted"]:
            break
    if args.preflight_only or not all(item["accepted"] for item in preflights):
        payload = {
            "accepted": len(preflights) == 3 and all(item["accepted"] for item in preflights),
            "preflights": preflights,
            "receipts": [],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["accepted"] else 1

    service_kwargs: dict[str, Any] = {}
    if args.db_path is not None:
        service_kwargs["db_path"] = args.db_path.expanduser()
    service = MutationService(**service_kwargs)

    common_mutation = {
        **common_read,
        "follow_timeout": args.follow_timeout,
    }
    probe_plan: list[tuple[str, str, Path | None, str]] = []
    if args.probe_async_write:
        probe_plan.append(("async_write_probe", args.short_thread, None, "async"))
    if args.include_short_mutation:
        probe_plan.append(("short", args.short_thread, None, "dom"))
    probe_plan.extend(
        [
            ("medium", args.medium_thread, None, "dom"),
            ("long", args.long_thread, None, "dom"),
            ("attachment", args.short_thread, attachment, "dom"),
        ]
    )
    receipts: list[dict[str, Any]] = []
    for label, thread_id, item_attachment, transport in probe_plan:
        if step_delay > 0:
            time.sleep(step_delay)
        receipts.append(
            _probe(
                service,
                label,
                thread_id,
                attachment=item_attachment,
                write_transport=transport,
                **common_mutation,
            )
        )
    payload = {
        "accepted": all(item["accepted"] for item in preflights)
        and all(receipt["accepted"] for receipt in receipts),
        "preflights": preflights,
        "receipts": receipts,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
