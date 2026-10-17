"""Redaction-first protocol observation schema and sanitizers.

This module captures structural request/response metadata for differential
comparison between browser/CDP and curl_cffi transports without persisting
cookie values, bearer tokens, device IDs, sentinel tokens, raw HTTP bodies,
or transcript contents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"(?:HTTP\s+)?(?P<status>[1-5][0-9]{2})", re.IGNORECASE)

_DEVICE_HEADER_NAMES = frozenset({
    "oai-did",
    "oai-device-id",
    "x-openai-device-id",
    "openai-device-id",
})
_ROUTE_HEADER_NAMES = frozenset({
    "x-openai-target-path",
    "x-openai-target-route",
    "oai-session-id",
})
_SENTINEL_HEADER_NAMES = frozenset({
    "openai-sentinel-chat-requirements-token",
    "openai-sentinel-arkose-token",
    "openai-sentinel-proof-token",
    "openai-sentinel-turnstile-token",
})
_ROUTE_COOKIE_NAMES = frozenset({
    "oai-chat-web-route",
    "__cf_bm",
    "cf_clearance",
    "_cfuvid",
})


def normalize_protocol_path(url_or_path: str) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return "/"
    parsed = urlsplit(raw)
    path = parsed.path or raw
    if not path.startswith("/"):
        path = "/" + path
    normalized = _UUID_RE.sub("<id>", path)
    normalized = re.sub(
        r"(/backend-(?:api|anon)/(?:f/)?conversations?/)(?!(?:init|prepare|gen_title|implicit_feedback|limit)(?:$|[/?#]))([^/?#]+)",
        r"\1<id>",
        normalized,
    )
    return normalized


def sanitize_headers(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    if not headers:
        return {
            "header_names": [],
            "has_authorization": False,
            "has_device_id": False,
            "has_route_headers": False,
            "has_sentinel_token": False,
            "has_origin": False,
            "has_referer": False,
            "has_content_type": False,
        }
    names = sorted({str(key) for key in headers.keys() if key})
    lower_names = {name.lower() for name in names}
    return {
        "header_names": names,
        "has_authorization": "authorization" in lower_names,
        "has_device_id": bool(lower_names & _DEVICE_HEADER_NAMES),
        "has_route_headers": bool(lower_names & _ROUTE_HEADER_NAMES),
        "has_sentinel_token": bool(
            (lower_names & _SENTINEL_HEADER_NAMES)
            or any(name.startswith("openai-sentinel-") for name in lower_names)
        ),
        "has_origin": "origin" in lower_names,
        "has_referer": "referer" in lower_names,
        "has_content_type": "content-type" in lower_names,
    }


def sanitize_cookies(
    cookies: Iterable[Any] | Mapping[str, Any] | None,
) -> dict[str, Any]:
    names: set[str] = set()
    domains: set[str] = set()
    if cookies is None:
        iterable: list[Any] = []
    elif isinstance(cookies, Mapping):
        iterable = [{"name": key} for key in cookies.keys()]
    elif hasattr(cookies, "jar"):
        try:
            iterable = list(cookies.jar)
        except Exception:
            iterable = []
    else:
        try:
            iterable = list(cookies)
        except Exception:
            iterable = []

    for item in iterable:
        if isinstance(item, str):
            if item.strip():
                names.add(item.strip())
            continue
        if isinstance(item, Mapping):
            name = item.get("name")
            domain = item.get("domain")
        else:
            name = getattr(item, "name", None)
            domain = getattr(item, "domain", None)
        if name:
            names.add(str(name))
        if domain:
            domains.add(str(domain))

    sorted_names = sorted(names)
    lower_names = {name.lower() for name in sorted_names}
    return {
        "cookie_names": sorted_names,
        "cookie_domains": sorted(domains),
        "has_route_cookie": bool(lower_names & _ROUTE_COOKIE_NAMES),
        "has_device_cookie": "oai-did" in lower_names,
    }


def _schema_of_value(value: Any, *, depth: int, max_depth: int) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, Mapping):
        if depth >= max_depth:
            return {"type": "object", "keys": sorted(str(k) for k in value.keys())}
        return {
            "type": "object",
            "keys": sorted(str(k) for k in value.keys()),
            "fields": {
                str(k): _schema_of_value(v, depth=depth + 1, max_depth=max_depth)
                for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            },
        }
    if isinstance(value, (list, tuple)):
        if not value:
            return {"type": "array", "count": 0, "item": None}
        return {
            "type": "array",
            "count": len(value),
            "item": _schema_of_value(value[0], depth=depth + 1, max_depth=max_depth),
        }
    return type(value).__name__


def sanitize_json_schema(
    payload: Any,
    *,
    max_depth: int = 2,
) -> tuple[list[str], dict[str, Any]]:
    """Extract top-level keys and value-free nested schema from a JSON payload."""
    parsed = payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except Exception:
            return [], {"type": "bytes"}
    elif isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return [], {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            return [], {"type": "non_json_text"}

    if isinstance(parsed, Mapping):
        keys = sorted(str(k) for k in parsed.keys())
        schema = _schema_of_value(parsed, depth=0, max_depth=max_depth)
        return keys, schema if isinstance(schema, dict) else {"type": str(schema)}
    if isinstance(parsed, (list, tuple)):
        schema = _schema_of_value(parsed, depth=0, max_depth=max_depth)
        return [], schema if isinstance(schema, dict) else {"type": str(schema)}
    return [], {}


def sanitize_sse_event_shapes(chunks: Iterable[str | bytes]) -> dict[str, Any]:
    """Summarize SSE stream shape without retaining message text or values."""
    data_lines = 0
    done_seen = False
    event_key_sets: set[str] = set()
    assistant_events = 0
    user_events = 0

    for chunk in chunks:
        text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_lines += 1
            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                done_seen = True
                continue
            try:
                decoded = json.loads(payload_text)
            except Exception:
                continue
            if isinstance(decoded, Mapping):
                keys = ",".join(sorted(str(k) for k in decoded.keys()))
                if keys:
                    event_key_sets.add(keys)
                msg = decoded.get("message")
                if isinstance(msg, Mapping):
                    author = msg.get("author")
                    role = author.get("role") if isinstance(author, Mapping) else None
                    if role == "assistant":
                        assistant_events += 1
                    elif role == "user":
                        user_events += 1

    return {
        "data_event_count": data_lines,
        "done_seen": done_seen,
        "event_key_signatures": sorted(event_key_sets),
        "assistant_message_events": assistant_events,
        "user_message_events": user_events,
    }


@dataclass(frozen=True)
class ProtocolObservation:
    transport: str
    operation: str
    method: str
    path: str
    status: int | None
    response_class: str
    header_names: list[str] = field(default_factory=list)
    cookie_names: list[str] = field(default_factory=list)
    cookie_domains: list[str] = field(default_factory=list)
    has_authorization: bool = False
    has_device_id: bool = False
    has_route_headers: bool = False
    has_route_cookie: bool = False
    has_sentinel_token: bool = False
    body_keys: list[str] = field(default_factory=list)
    body_schema: dict[str, Any] = field(default_factory=dict)
    sse_shape: dict[str, Any] = field(default_factory=dict)
    sequence_index: int = 0
    elapsed_ms: float | None = None
    bootstrap_stage: str | None = None

    @property
    def adapter(self) -> str:
        return self.transport

    @property
    def route(self) -> str:
        return self.path

    @property
    def auth_header_present(self) -> bool:
        return self.has_authorization

    @property
    def device_id_present(self) -> bool:
        return self.has_device_id

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["adapter"] = self.transport
        payload["route"] = self.path
        payload["auth_header_present"] = self.has_authorization
        payload["device_id_present"] = self.has_device_id
        return payload


def build_protocol_observation(
    *,
    transport: str,
    operation: str,
    method: str,
    path: str,
    status: int | None,
    response_class: str,
    headers: Mapping[str, Any] | None = None,
    cookies: Iterable[Any] | Mapping[str, Any] | None = None,
    cookie_domains: Iterable[str] | None = None,
    payload: Any = None,
    sse_chunks: Iterable[str | bytes] | None = None,
    sequence_index: int = 0,
    elapsed_ms: float | None = None,
    bootstrap_stage: str | None = None,
) -> ProtocolObservation:
    header_meta = sanitize_headers(headers)
    cookie_meta = sanitize_cookies(cookies)
    domains = sorted(
        set(cookie_meta["cookie_domains"])
        | {str(d) for d in (cookie_domains or ()) if d}
    )
    body_keys, body_schema = sanitize_json_schema(payload) if payload is not None else ([], {})
    sse_shape = sanitize_sse_event_shapes(sse_chunks) if sse_chunks is not None else {}
    return ProtocolObservation(
        transport=str(transport),
        operation=str(operation),
        method=str(method).upper(),
        path=normalize_protocol_path(path),
        status=status,
        response_class=str(response_class),
        header_names=list(header_meta["header_names"]),
        cookie_names=list(cookie_meta["cookie_names"]),
        cookie_domains=domains,
        has_authorization=bool(header_meta["has_authorization"]),
        has_device_id=bool(header_meta["has_device_id"]),
        has_route_headers=bool(header_meta["has_route_headers"]),
        has_route_cookie=bool(cookie_meta["has_route_cookie"]),
        has_sentinel_token=bool(header_meta["has_sentinel_token"]),
        body_keys=body_keys,
        body_schema=body_schema,
        sse_shape=sse_shape,
        sequence_index=int(sequence_index),
        elapsed_ms=round(float(elapsed_ms), 3) if elapsed_ms is not None else None,
        bootstrap_stage=bootstrap_stage,
    )


def status_from_exception(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    match = _STATUS_RE.search(str(exc))
    return int(match.group("status")) if match else None


def observation_from_exception(
    exc: BaseException,
    *,
    transport: str,
    operation: str,
    method: str,
    path: str,
    headers: Mapping[str, Any] | None = None,
    cookies: Iterable[Any] | Mapping[str, Any] | None = None,
    cookie_domains: Iterable[str] | None = None,
    payload: Any = None,
    sse_chunks: Iterable[str | bytes] | None = None,
    sequence_index: int = 0,
    elapsed_ms: float | None = None,
    bootstrap_stage: str | None = None,
) -> ProtocolObservation:
    return build_protocol_observation(
        transport=transport,
        operation=operation,
        method=method,
        path=path,
        status=status_from_exception(exc),
        response_class=exc.__class__.__name__,
        headers=headers,
        cookies=cookies,
        cookie_domains=cookie_domains,
        payload=payload,
        sse_chunks=sse_chunks,
        sequence_index=sequence_index,
        elapsed_ms=elapsed_ms,
        bootstrap_stage=bootstrap_stage,
    )
