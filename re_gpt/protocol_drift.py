"""Structural compatibility fingerprints for redacted ChatGPT protocol observations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .protocol_observation import normalize_protocol_path

_SCHEMA_VERSION = 1
_ATTACHMENT_STAGES = frozenset({"attachment_staging", "attachment_upload", "attachment_process"})


def _canonical_schema(value: Any) -> str | None:
    if not isinstance(value, Mapping) or not value:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _norm_obs(item: Mapping[str, Any]) -> dict[str, Any]:
    method = str(item.get("method") or "GET").upper()
    path = normalize_protocol_path(str(item.get("path") or item.get("route") or "/"))
    stage = str(item.get("bootstrap_stage") or "") or None
    headers = sorted({str(x).lower() for x in item.get("header_names") or [] if x})
    body_keys = sorted({str(x) for x in item.get("body_keys") or [] if x})
    body_schema = _canonical_schema(item.get("body_schema"))
    sse = item.get("sse_shape") if isinstance(item.get("sse_shape"), Mapping) else {}
    event_keys = sorted({str(x) for x in sse.get("event_key_signatures") or [] if x})
    return {
        "method": method,
        "path": path,
        "stage": stage,
        "header_names": headers,
        "body_keys": body_keys,
        "body_schema": body_schema,
        "sse_event_key_signatures": event_keys,
    }


def build_protocol_fingerprint(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [_norm_obs(item) for item in observations if isinstance(item, Mapping)]
    routes = sorted(
        ({"method": item["method"], "path": item["path"], "stage": item["stage"]} for item in normalized),
        key=lambda x: (str(x["stage"] or ""), x["method"], x["path"]),
    )
    dedup_routes: list[dict[str, Any]] = []
    seen_routes: set[tuple[str, str, str | None]] = set()
    for route in routes:
        key = (route["method"], route["path"], route["stage"])
        if key not in seen_routes:
            seen_routes.add(key)
            dedup_routes.append(route)

    header_names = sorted({name for item in normalized for name in item["header_names"]})
    body_keys = sorted({name for item in normalized for name in item["body_keys"]})
    body_schema_signatures = sorted({
        json.dumps(
            {"method": item["method"], "path": item["path"], "stage": item["stage"], "schema": item["body_schema"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in normalized
        if item["body_schema"] is not None
    })
    event_keys = sorted({name for item in normalized for name in item["sse_event_key_signatures"]})
    stages = sorted({str(item["stage"]) for item in normalized if item["stage"]})
    attachment_stages = sorted(set(stages) & _ATTACHMENT_STAGES)
    return {
        "schema_version": _SCHEMA_VERSION,
        "routes": dedup_routes,
        "stages": stages,
        "attachment_stages": attachment_stages,
        "header_names": header_names,
        "body_keys": body_keys,
        "body_schema_signatures": body_schema_signatures,
        "sse_event_key_signatures": event_keys,
    }


def _route_keys(fp: Mapping[str, Any]) -> set[tuple[str, str, str | None]]:
    out: set[tuple[str, str, str | None]] = set()
    for item in fp.get("routes") or []:
        if not isinstance(item, Mapping):
            continue
        out.add((str(item.get("method") or ""), str(item.get("path") or ""), str(item.get("stage")) if item.get("stage") is not None else None))
    return out


def _sorted_routes(values: set[tuple[str, str, str | None]]) -> list[dict[str, Any]]:
    return [
        {"method": method, "path": path, "stage": stage}
        for method, path, stage in sorted(values, key=lambda x: (str(x[2] or ""), x[0], x[1]))
    ]


def compare_protocol_fingerprints(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    base_routes = _route_keys(baseline)
    cand_routes = _route_keys(candidate)
    base_headers = {str(x).lower() for x in baseline.get("header_names") or []}
    cand_headers = {str(x).lower() for x in candidate.get("header_names") or []}
    base_body = {str(x) for x in baseline.get("body_keys") or []}
    cand_body = {str(x) for x in candidate.get("body_keys") or []}
    base_schema = {str(x) for x in baseline.get("body_schema_signatures") or []}
    cand_schema = {str(x) for x in candidate.get("body_schema_signatures") or []}
    base_events = {str(x) for x in baseline.get("sse_event_key_signatures") or []}
    cand_events = {str(x) for x in candidate.get("sse_event_key_signatures") or []}
    base_attach = {str(x) for x in baseline.get("attachment_stages") or []}
    cand_attach = {str(x) for x in candidate.get("attachment_stages") or []}

    delta = {
        "missing_routes": _sorted_routes(base_routes - cand_routes),
        "added_routes": _sorted_routes(cand_routes - base_routes),
        "missing_header_names": sorted(base_headers - cand_headers),
        "added_header_names": sorted(cand_headers - base_headers),
        "missing_body_keys": sorted(base_body - cand_body),
        "added_body_keys": sorted(cand_body - base_body),
        "missing_body_schema_signatures": sorted(base_schema - cand_schema),
        "added_body_schema_signatures": sorted(cand_schema - base_schema),
        "missing_sse_event_key_signatures": sorted(base_events - cand_events),
        "added_sse_event_key_signatures": sorted(cand_events - base_events),
        "missing_attachment_stages": sorted(base_attach - cand_attach),
        "added_attachment_stages": sorted(cand_attach - base_attach),
    }

    incompatible = bool(
        delta["missing_routes"]
        or delta["missing_body_keys"]
        or delta["missing_body_schema_signatures"]
        or delta["missing_attachment_stages"]
    )
    changed = any(bool(value) for value in delta.values())
    classification = "incompatible" if incompatible else ("drift_suspected" if changed else "compatible")
    return {
        "classification": classification,
        "compatible": classification == "compatible",
        "schema_version": _SCHEMA_VERSION,
        "delta": delta,
    }


def compare_protocol_observations(
    baseline_observations: Sequence[Mapping[str, Any]],
    candidate_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = build_protocol_fingerprint(baseline_observations)
    candidate = build_protocol_fingerprint(candidate_observations)
    return {
        "baseline": baseline,
        "candidate": candidate,
        **compare_protocol_fingerprints(baseline, candidate),
    }
