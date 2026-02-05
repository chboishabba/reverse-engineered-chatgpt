"""Utilities shared between interactive CLI and automation scripts."""

from __future__ import annotations

import re
import shlex
from typing import Optional, Tuple

_CONVERSATION_ID_RE = re.compile(
    r"(?i)(?:^|/)(?:c/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)"
)
_UUID_RE = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def extract_conversation_id(value: str) -> Optional[str]:
    """Extract a conversation UUID from a ChatGPT URL or raw ID."""

    if not value:
        return None

    trimmed = value.strip().rstrip(",;:.!?")
    if _UUID_RE.fullmatch(trimmed):
        return trimmed

    match = _CONVERSATION_ID_RE.search(trimmed)
    if match:
        return match.group(1)

    return None


def normalize_conversation_selector(value: str) -> str:
    """Return a conversation ID if one can be extracted from *value*."""

    extracted = extract_conversation_id(value)
    return extracted or value


def parse_lines_range(value: str) -> Optional[Tuple[int, Optional[int]]]:
    """Turn a ``lines`` expression into (start, end) numbers."""

    cleaned = value.strip().rstrip(",;:.!?")
    if not cleaned:
        return None

    plus_match = re.fullmatch(r"(\d+)\s*\+", cleaned)
    if plus_match:
        start = int(plus_match.group(1))
        if start < 1:
            return None
        return start, None

    dash_match = re.fullmatch(r"(\d+)\s*-\s*(\d*)", cleaned)
    if dash_match:
        start = int(dash_match.group(1))
        end_str = dash_match.group(2)
        end = int(end_str) if end_str else None
        if start < 1 or (end is not None and end < start):
            return None
        return start, end

    digits_match = re.fullmatch(r"(\d+)", cleaned)
    if digits_match:
        start = int(digits_match.group(1))
        if start < 1:
            return None
        return start, start

    return None


def parse_view_argument(argument: str) -> tuple[str, Optional[Tuple[int, Optional[int]]], bool]:
    """Extract the selector, line range token, and `since last update` flag."""

    if not argument:
        return "", None, False

    try:
        tokens = shlex.split(argument)
    except ValueError:
        tokens = argument.split()

    selector_tokens: list[str] = []
    lines_range: Optional[Tuple[int, Optional[int]]] = None
    since_last_update = False
    i = 0

    while i < len(tokens):
        token = tokens[i]
        lowered = token.lower().rstrip(",;:.!?")

        if lowered == "lines" and i + 1 < len(tokens):
            parsed = parse_lines_range(tokens[i + 1])
            if parsed:
                lines_range = parsed
                i += 2
                continue

        if lowered.startswith("lines=") or lowered.startswith("lines:"):
            separator = "=" if "=" in token else ":"
            remainder = token.split(separator, 1)[1]
            parsed = parse_lines_range(remainder)
            if parsed:
                lines_range = parsed
                i += 1
                continue

        normalized_since = lowered.replace("_", "-")
        if normalized_since in {"since-last-update", "since-last-updates"}:
            since_last_update = True
            i += 1
            continue

        if lowered == "since" and i + 2 < len(tokens):
            next_token = tokens[i + 1].lower().rstrip(",;:.!?")
            next_next = tokens[i + 2].lower().rstrip(",;:.!?")
            if next_token == "last" and next_next.startswith("up"):
                since_last_update = True
                i += 3
                continue

        selector_tokens.append(token)
        i += 1

    selector = " ".join(selector_tokens).strip()
    return normalize_conversation_selector(selector), lines_range, since_last_update
