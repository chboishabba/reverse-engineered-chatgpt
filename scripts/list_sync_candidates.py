#!/usr/bin/env python3
"""List live ChatGPT conversations that are missing/stale in an archive DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from re_gpt.errors import InvalidSessionToken, TokenNotProvided
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.utils import get_session_token


def _coerce_timestamp(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return float(trimmed)
        except ValueError:
            pass
        normalized = trimmed[:-1] + "+00:00" if trimmed.endswith("Z") else trimmed
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _format_timestamp(epoch_value: Optional[float]) -> str:
    if epoch_value is None:
        return ""
    return datetime.fromtimestamp(epoch_value, tz=timezone.utc).isoformat()


def _normalize_title(title: object) -> str:
    raw = str(title or "")
    return " ".join(raw.strip().lower().split())


def _load_archive_index(
    db_path: Path,
    *,
    platform: str,
    account_id: str,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    by_source_thread_id: Dict[str, float] = {}
    by_title: Dict[str, float] = {}

    with sqlite3.connect(str(db_path)) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT source_thread_id, MAX(ts)
            FROM messages
            WHERE platform = ?
              AND account_id = ?
              AND source_thread_id IS NOT NULL
              AND TRIM(source_thread_id) != ''
            GROUP BY source_thread_id
            """,
            (platform, account_id),
        )
        for source_thread_id, max_ts in cursor.fetchall():
            key = str(source_thread_id or "").strip()
            ts_value = _coerce_timestamp(max_ts)
            if key and ts_value is not None:
                by_source_thread_id[key] = ts_value

        cursor.execute(
            """
            SELECT LOWER(TRIM(title)) AS norm_title, MAX(ts)
            FROM messages
            WHERE platform = ?
              AND account_id = ?
              AND title IS NOT NULL
              AND TRIM(title) != ''
            GROUP BY norm_title
            """,
            (platform, account_id),
        )
        for norm_title, max_ts in cursor.fetchall():
            key = str(norm_title or "").strip()
            ts_value = _coerce_timestamp(max_ts)
            if key and ts_value is not None:
                by_title[key] = ts_value

    return by_source_thread_id, by_title


def _iter_live_conversations(
    chatgpt: SyncChatGPT,
    *,
    page_size: int,
    max_pages: Optional[int],
) -> Iterator[dict]:
    offset = 0
    pages = 0
    while True:
        if max_pages is not None and pages >= max_pages:
            break
        page_data = chatgpt.list_conversations_page(offset=offset, limit=page_size)
        items = page_data.get("items", []) if isinstance(page_data, dict) else []
        if not items:
            break
        for item in items:
            if isinstance(item, dict):
                yield item
        pages += 1
        offset += len(items)
        if len(items) < page_size:
            break


@dataclass
class Candidate:
    status: str
    conversation_id: str
    title: str
    live_update_time: Optional[float]
    archive_update_time: Optional[float]
    matched_by: str

    def to_row(self) -> List[str]:
        return [
            self.status,
            self.conversation_id,
            self.title,
            _format_timestamp(self.live_update_time),
            _format_timestamp(self.archive_update_time),
            self.matched_by,
        ]


def _build_candidates(
    live_conversations: Iterable[dict],
    *,
    by_source_thread_id: Dict[str, float],
    by_title: Dict[str, float],
    stale_threshold_sec: float,
    title_only: bool,
) -> Tuple[List[Candidate], int]:
    candidates: List[Candidate] = []
    scanned = 0
    seen_ids: set[str] = set()

    for item in live_conversations:
        conversation_id = str(item.get("id") or "").strip()
        if not conversation_id or conversation_id in seen_ids:
            continue
        seen_ids.add(conversation_id)
        scanned += 1

        title = str(item.get("title") or "").strip()
        live_update = _coerce_timestamp(item.get("update_time") or item.get("last_updated"))

        matched_by = "none"
        archive_update: Optional[float] = None

        if not title_only and conversation_id in by_source_thread_id:
            matched_by = "source_thread_id"
            archive_update = by_source_thread_id[conversation_id]
        else:
            normalized_title = _normalize_title(title)
            if normalized_title in by_title:
                matched_by = "title"
                archive_update = by_title[normalized_title]

        if archive_update is None:
            candidates.append(
                Candidate(
                    status="missing",
                    conversation_id=conversation_id,
                    title=title,
                    live_update_time=live_update,
                    archive_update_time=None,
                    matched_by="none",
                )
            )
            continue

        if live_update is None:
            continue

        if live_update > (archive_update + stale_threshold_sec):
            candidates.append(
                Candidate(
                    status="stale",
                    conversation_id=conversation_id,
                    title=title,
                    live_update_time=live_update,
                    archive_update_time=archive_update,
                    matched_by=matched_by,
                )
            )

    return candidates, scanned


def _print_table(candidates: List[Candidate]) -> None:
    headers = ["status", "conversation_id", "title", "live_update", "archive_update", "matched_by"]
    rows = [headers] + [candidate.to_row() for candidate in candidates]
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    for index, row in enumerate(rows):
        print("  ".join(part.ljust(widths[i]) for i, part in enumerate(row)))
        if index == 0:
            print("  ".join("-" * widths[i] for i in range(len(headers))))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare live ChatGPT conversation metadata against a SQLite archive and "
            "print conversations that are missing or stale."
        )
    )
    parser.add_argument(
        "--archive-db",
        required=True,
        help="Path to archive SQLite DB (for example ../chat-export-structurer/my_archive.sqlite).",
    )
    parser.add_argument("--token", help="Session token value (defaults to config.ini or ~/.chatgpt_session).")
    parser.add_argument("--platform", default="chatgpt", help="Archive platform filter (default: chatgpt).")
    parser.add_argument("--account", default="main", help="Archive account filter (default: main).")
    parser.add_argument("--page-size", type=int, default=28, help="Live catalog page size (default: 28).")
    parser.add_argument("--max-pages", type=int, help="Optional cap on fetched live pages.")
    parser.add_argument(
        "--stale-threshold-sec",
        type=float,
        default=0.0,
        help="Minimum positive delta before a conversation is considered stale (default: 0).",
    )
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="Match archive rows by normalized title only (skip source_thread_id matching).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "tsv", "ids", "json"),
        default="table",
        help="Output format (default: table).",
    )
    args = parser.parse_args()

    db_path = Path(args.archive_db).expanduser()
    if not db_path.exists():
        print(f"Archive DB not found: {db_path}", file=sys.stderr)
        return 2

    try:
        token = args.token or get_session_token()
    except TokenNotProvided:
        print("No session token found. Provide --token or add config.ini/~/.chatgpt_session.", file=sys.stderr)
        return 2

    try:
        by_source_thread_id, by_title = _load_archive_index(
            db_path,
            platform=args.platform,
            account_id=args.account,
        )

        with SyncChatGPT(session_token=token) as chatgpt:
            if not getattr(chatgpt, "auth_token", None):
                raise InvalidSessionToken

            live_conversations = _iter_live_conversations(
                chatgpt,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
            candidates, scanned = _build_candidates(
                live_conversations,
                by_source_thread_id=by_source_thread_id,
                by_title=by_title,
                stale_threshold_sec=max(0.0, float(args.stale_threshold_sec)),
                title_only=bool(args.title_only),
            )
    except InvalidSessionToken:
        print("Session token rejected. Please refresh __Secure-next-auth.session-token.", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to list sync candidates: {exc}", file=sys.stderr)
        return 1

    missing_count = sum(1 for c in candidates if c.status == "missing")
    stale_count = sum(1 for c in candidates if c.status == "stale")
    print(
        (
            f"scanned={scanned} candidates={len(candidates)} "
            f"missing={missing_count} stale={stale_count}"
        ),
        file=sys.stderr,
    )

    if args.format == "ids":
        for candidate in candidates:
            print(candidate.conversation_id)
    elif args.format == "tsv":
        print("\t".join(["status", "conversation_id", "title", "live_update", "archive_update", "matched_by"]))
        for candidate in candidates:
            print("\t".join(candidate.to_row()))
    elif args.format == "json":
        payload = [
            {
                **asdict(candidate),
                "live_update_iso": _format_timestamp(candidate.live_update_time),
                "archive_update_iso": _format_timestamp(candidate.archive_update_time),
            }
            for candidate in candidates
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_table(candidates)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
