#!/usr/bin/env python3
"""High-throughput ChatGPT puller + sync/async benchmark into structurer DB.

Default behavior:
- fetch live conversations via re_gpt
- ingest directly into ~/chat_archive.sqlite
- do not write intermediate JSON exports
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import re
import sqlite3
import sys
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


ROOT = Path(__file__).resolve().parents[2]
STRUCTURER_INGEST = ROOT / "chat-export-structurer" / "src" / "ingest.py"
STRUCTURER_SRC = ROOT / "chat-export-structurer" / "src"

if str(ROOT / "reverse-engineered-chatgpt") not in sys.path:
    sys.path.insert(0, str(ROOT / "reverse-engineered-chatgpt"))

from re_gpt.async_chatgpt import AsyncChatGPT
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.utils import get_session_token


@dataclass
class FetchTarget:
    conversation_id: str
    title: str
    update_time: Optional[float]


@dataclass
class FetchResult:
    conversation_id: str
    title: str
    update_time: Optional[float]
    ok: bool
    duration_s: float
    message_count: int
    error: str = ""
    messages: tuple[dict[str, Any], ...] = ()
    requested_conversation_id: str = ""
    payload_conversation_id: Optional[str] = None
    identity_verified: bool = False
    artifacts: tuple[dict[str, Any], ...] = ()


class FetchProgressReporter:
    def __init__(self, *, total: int, engine: str, enabled: bool = True) -> None:
        self.total = max(0, int(total))
        self.engine = engine
        self.enabled = enabled
        self.started = time.monotonic()
        self.completed = 0
        self.ok = 0
        self.failures = 0
        self.messages = 0
        self.in_flight = 0
        self._active_started: list[float] = []
        self._completed_duration_s = 0.0
        self._last_emit = 0.0
        self._lock = threading.Lock()

    def start_target(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._active_started.append(time.monotonic())
            self.in_flight += 1
            self._emit_locked("starting", force=self.completed == 0)

    def finish_target(self, *, ok: bool, message_count: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            started = self._active_started.pop(0) if self._active_started else now
            self._completed_duration_s += max(0.0, now - started)
            self.in_flight = max(0, self.in_flight - 1)
            self.completed += 1
            self.messages += max(0, int(message_count))
            if ok:
                self.ok += 1
            else:
                self.failures += 1
            self._emit_locked("progress", force=True)

    def finish(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._emit_locked("complete", force=True)

    def _emit_locked(self, stage: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._last_emit and (now - self._last_emit) < 1.0:
            return
        elapsed = max(0.001, now - self.started)
        rate = self.completed / elapsed
        if rate <= 0.0 and self.in_flight > 0:
            avg_active_age = sum(max(0.0, now - started) for started in self._active_started) / max(1, len(self._active_started))
            if avg_active_age >= 1.0:
                rate = self.in_flight / avg_active_age
        eta = 0.0
        if self.completed < self.total and rate > 0.0:
            eta = (self.total - self.completed) / rate
        line = (
            f"[fetch:{self.engine}] status={stage} done={self.completed}/{self.total} "
            f"in_flight={self.in_flight} ok={self.ok} err={self.failures} msgs={self.messages} "
            f"rate={rate:.2f} conv/s elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}"
        )
        print(line, file=sys.stderr, flush=True)
        self._last_emit = now


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class SyncRateLimiter:
    def __init__(self, rps: float) -> None:
        self.interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.interval <= 0.0:
            return
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
            now = time.monotonic()
        self._next_allowed = now + self.interval


class AsyncRateLimiter:
    def __init__(self, rps: float) -> None:
        self.interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.interval <= 0.0:
            return
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
                now = time.monotonic()
            self._next_allowed = now + self.interval


def _normalize_title(value: object) -> str:
    raw = str(value or "")
    return " ".join(raw.strip().lower().split())


def _looks_like_conversation_id(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    parts = text.split("-")
    if len(parts) != 5:
        return False
    expected = (8, 4, 4, 4, 12)
    if any(len(part) != expected[idx] for idx, part in enumerate(parts)):
        return False
    allowed = set("0123456789abcdefABCDEF")
    return all(ch in allowed for ch in text.replace("-", ""))


def _parse_csv_values(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_value_file(path_value: Optional[str]) -> list[str]:
    values: list[str] = []
    if not path_value:
        return values
    path = Path(path_value).expanduser()
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        values.append(line)
    return values


def _parse_selector_file(path_value: Optional[str]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    titles: list[str] = []
    if not path_value:
        return ids, titles
    path = Path(path_value).expanduser()
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            prefix, remainder = line.split(":", 1)
            prefix = prefix.strip().lower()
            remainder = remainder.strip()
            if not remainder:
                continue
            if prefix in {"id", "conversation_id"}:
                ids.append(remainder)
                continue
            if prefix in {"title", "name"}:
                titles.append(remainder)
                continue
        if _looks_like_conversation_id(line):
            ids.append(line)
        else:
            titles.append(line)
    return ids, titles


def _iso_to_epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _load_structurer_ingest() -> Any:
    if not STRUCTURER_INGEST.exists():
        raise FileNotFoundError(f"Missing ingest.py: {STRUCTURER_INGEST}")
    spec = importlib.util.spec_from_file_location("chat_export_structurer_ingest", str(STRUCTURER_INGEST))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load spec from {STRUCTURER_INGEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "ingest_parsed_messages", None)):
        raise RuntimeError("ingest.py does not expose ingest_parsed_messages()")
    return module


def _load_chatgpt_parser() -> Callable[[dict[str, Any]], str]:
    if str(STRUCTURER_SRC) not in sys.path:
        sys.path.insert(0, str(STRUCTURER_SRC))
    try:
        mod = importlib.import_module("parsers.chatgpt")
        fn = getattr(mod, "extract_text_from_content", None)
        if callable(fn):
            return fn
    except Exception:
        pass

    def _fallback(content: dict[str, Any]) -> str:
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts")
        if not isinstance(parts, list):
            return ""
        out: list[str] = []
        for part in parts:
            if isinstance(part, str):
                txt = part.strip()
                if txt:
                    out.append(txt)
            elif isinstance(part, dict):
                text = str(part.get("text") or part.get("content") or part.get("title") or "").strip()
                if text:
                    out.append(text)
                else:
                    out.append("[[part]]")
        return "\n".join(out)

    return _fallback


def _extract_messages_for_ingest(chat: dict[str, Any], *, extract_text: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    mapping = chat.get("mapping")
    title = str(chat.get("title") or "")
    if not isinstance(mapping, dict):
        return []

    current_node = str(chat.get("current_node") or "")
    active_nodes: set[str] = set()
    cursor = current_node
    seen: set[str] = set()
    while cursor and cursor not in seen and cursor in mapping:
        seen.add(cursor)
        active_nodes.add(cursor)
        parent = mapping.get(cursor, {}).get("parent") if isinstance(mapping.get(cursor), dict) else None
        cursor = str(parent or "")
    if not current_node:
        active_nodes = {str(node_id) for node_id in mapping}

    def metadata_value(message: dict[str, Any], *keys: str) -> str:
        containers = [message, message.get("metadata") if isinstance(message.get("metadata"), dict) else {}]
        for container in containers:
            for key in keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def classify(message: dict[str, Any], content: str) -> dict[str, str]:
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = str(author.get("role") or "")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        token_match = re.search(r"filecite[^\s]*", content, flags=re.IGNORECASE)
        citation_token = token_match.group(0) if token_match else metadata_value(message, "citation_token", "filecite")
        filename = metadata_value(message, "filename", "file_name", "source_name", "name")
        pointer = metadata_value(message, "asset_pointer", "image_url", "file_id")
        if not pointer:
            pointer_match = re.search(r"(?:file-service|fileservice|sediment)://[A-Za-z0-9._-]+", content, flags=re.IGNORECASE)
            pointer = pointer_match.group(0) if pointer_match else ""
        if not pointer and isinstance(metadata.get("attachments"), list):
            for attachment in metadata["attachments"]:
                if isinstance(attachment, dict):
                    pointer = str(attachment.get("asset_pointer") or attachment.get("id") or "")
                    filename = filename or str(attachment.get("filename") or attachment.get("name") or "")
                    break
        tool_kind = ""
        source_scope = ""
        if role in {"tool", "system"}:
            tool_kind = "tool"
            if citation_token or "NotebookLM Source" in content or "filecite" in content:
                tool_kind = "file_context"
                source_scope = "project_file_context"
            elif pointer or "jupyter" in json.dumps(metadata, sort_keys=True).lower():
                tool_kind = "artifact_output"
                source_scope = "generated_or_programmatic_artifact"
            else:
                source_scope = "tool_output"
        return {
            "tool_kind": tool_kind,
            "citation_token": citation_token,
            "filename": filename,
            "asset_pointer": pointer,
            "source_scope": source_scope,
            "visibility": "compact" if tool_kind == "file_context" else "visible",
        }

    messages: list[dict[str, Any]] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue

        author = message.get("author")
        role = ""
        if isinstance(author, dict):
            role = str(author.get("role") or "")

        content_obj = message.get("content")
        if not isinstance(content_obj, dict):
            continue
        content = extract_text(content_obj)

        ts = message.get("create_time")
        if ts is None:
            ts = message.get("update_time")
        try:
            created_at = float(ts)
        except (TypeError, ValueError):
            continue

        source_message_id = str(message.get("id") or node_id or "")
        classification = classify(message, content)

        messages.append(
            {
                "thread_title": title,
                "role": role,
                "content": content,
                "created_at": created_at,
                "source_message_id": source_message_id,
                "node_id": str(node_id),
                "parent_node_id": str(node.get("parent") or ""),
                "branch_membership": "active" if node_id in active_nodes else "inactive",
                "body_storage_ref": source_message_id,
                "mime_type": metadata_value(message, "mime_type", "mimeType"),
                "provenance_refs": [{"kind": classification["source_scope"]}] if classification["source_scope"] else [],
                **classification,
            }
        )

    messages.sort(key=lambda item: item.get("created_at") or 0)
    return messages


def _asset_artifact_inputs(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    artifacts: list[dict[str, Any]] = []
    for message in messages:
        pointer = str(message.get("asset_pointer") or "").strip()
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        artifacts.append({
            "artifact_id": pointer.rsplit("/", 1)[-1],
            "kind": "programmatic_image" if message.get("tool_kind") == "artifact_output" else "generated_image",
            "mime_type": str(message.get("mime_type") or "image/png"),
            "asset_pointer": pointer,
            "source_message_id": str(message.get("source_message_id") or ""),
        })
    return artifacts


def _save_asset_records(
    records: list[dict[str, Any]], *, conversation_id: str, downloader: Callable[[str], Any]
) -> tuple[dict[str, Any], ...]:
    root = Path.home() / "chat_archive_artifacts" / conversation_id
    output: list[dict[str, Any]] = []
    for record in records:
        try:
            download = downloader(str(record["asset_pointer"]))
            content = getattr(download, "content", None)
            content_type = getattr(download, "content_type", None)
            if content is None and isinstance(download, tuple):
                content, content_type = download
            if not isinstance(content, (bytes, bytearray)):
                raise TypeError("asset downloader returned no binary content")
            digest = hashlib.sha256(content).hexdigest()
            extension = ".png" if str(content_type or record.get("mime_type")) == "image/png" else ".bin"
            path = root / f"{record['artifact_id']}{extension}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            output.append({
                **record,
                "local_path": str(path),
                "mime_type": str(content_type or record.get("mime_type") or "application/octet-stream"),
                "size_bytes": len(content),
                "sha256": digest,
            })
        except Exception as exc:  # noqa: BLE001 - preserve failed artifact as metadata
            output.append({**record, "download_error": str(exc)})
    return tuple(output)


async def _save_asset_records_async(
    records: list[dict[str, Any]], *, conversation_id: str, downloader: Callable[[str], Any]
) -> tuple[dict[str, Any], ...]:
    async def fetch(pointer: str) -> Any:
        value = downloader(pointer)
        return await value if hasattr(value, "__await__") else value

    root = Path.home() / "chat_archive_artifacts" / conversation_id
    output: list[dict[str, Any]] = []
    for record in records:
        try:
            download = await fetch(str(record["asset_pointer"]))
            content, content_type = download
            if not isinstance(content, (bytes, bytearray)):
                raise TypeError("asset downloader returned no binary content")
            digest = hashlib.sha256(content).hexdigest()
            extension = ".png" if str(content_type or record.get("mime_type")) == "image/png" else ".bin"
            path = root / f"{record['artifact_id']}{extension}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            output.append({**record, "local_path": str(path), "mime_type": str(content_type or record.get("mime_type") or "application/octet-stream"), "size_bytes": len(content), "sha256": digest})
        except Exception as exc:  # noqa: BLE001
            output.append({**record, "download_error": str(exc)})
    return tuple(output)


def _payload_conversation_id(chat: dict[str, Any]) -> Optional[str]:
    for key in ("id", "conversation_id"):
        value = chat.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _validate_payload_identity(
    chat: dict[str, Any],
    requested_conversation_id: str,
) -> tuple[Optional[str], bool, str]:
    payload_id = _payload_conversation_id(chat)
    requested_id = str(requested_conversation_id or "").strip()
    if payload_id is None:
        return None, False, "payload conversation identity is missing"
    if payload_id != requested_id:
        return payload_id, False, (
            "payload conversation identity mismatch: "
            f"requested={requested_id} payload={payload_id}"
        )
    return payload_id, True, ""


def _load_existing_updates(db_path: Path, *, account_id: str) -> dict[str, float]:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if cur.fetchone() is None:
            return {}
        cur.execute(
            """
            SELECT source_thread_id, MAX(ts)
            FROM messages
            WHERE platform = 'chatgpt'
              AND account_id = ?
              AND source_thread_id IS NOT NULL
              AND TRIM(source_thread_id) <> ''
            GROUP BY source_thread_id
            """,
            (account_id,),
        )
        rows = cur.fetchall()
    finally:
        con.close()

    out: dict[str, float] = {}
    for source_thread_id, max_ts in rows:
        key = str(source_thread_id or "").strip()
        if not key:
            continue
        epoch = _iso_to_epoch(max_ts)
        if epoch is not None:
            out[key] = epoch
    return out


def _discover_targets(
    token: str,
    *,
    ids: list[str],
    titles: list[str],
    title_match: str,
    limit: int,
    page_size: int,
    max_pages: Optional[int],
    skip_existing: bool,
    existing_updates: dict[str, float],
    debug: bool,
) -> list[FetchTarget]:
    if ids and not titles:
        return [FetchTarget(conversation_id=i, title="", update_time=None) for i in ids]

    targets: list[FetchTarget] = []
    seen_target_ids: set[str] = set()
    id_selectors = {value.strip() for value in ids if value.strip()}
    normalized_title_selectors: list[str] = []
    for title in titles:
        normalized = _normalize_title(title)
        if normalized and normalized not in normalized_title_selectors:
            normalized_title_selectors.append(normalized)

    with SyncChatGPT(session_token=token) as chatgpt:
        offset = 0
        pages = 0
        matched_ids: set[str] = set()
        matched_titles: set[str] = set()
        while True:
            if max_pages is not None and pages >= max_pages:
                break
            page = chatgpt.list_conversations_page(offset=offset, limit=page_size)
            items = page.get("items", []) if isinstance(page, dict) else []
            if not items:
                break
            pages += 1
            if debug:
                print(f"[discover] page={pages} offset={offset} items={len(items)}")
            for item in items:
                cid = str(item.get("id") or "").strip()
                if not cid:
                    continue
                title = str(item.get("title") or "")
                normalized_title = _normalize_title(title)
                update = _iso_to_epoch(item.get("update_time") or item.get("last_updated"))

                if id_selectors or normalized_title_selectors:
                    id_hit = cid in id_selectors
                    title_hit = False
                    if normalized_title_selectors:
                        if title_match == "contains":
                            matched_selectors = [sel for sel in normalized_title_selectors if sel in normalized_title]
                            title_hit = bool(matched_selectors)
                            if title_hit:
                                matched_titles.update(matched_selectors)
                        else:
                            title_hit = normalized_title in normalized_title_selectors
                            if title_hit:
                                matched_titles.add(normalized_title)
                    if id_hit:
                        matched_ids.add(cid)
                    if not (id_hit or title_hit):
                        continue

                if skip_existing and update is not None:
                    cached_update = existing_updates.get(cid)
                    if cached_update is not None and cached_update >= update:
                        continue

                if cid in seen_target_ids:
                    continue
                seen_target_ids.add(cid)
                targets.append(FetchTarget(conversation_id=cid, title=title, update_time=update))
                if limit > 0 and len(targets) >= limit:
                    return targets

            if (
                title_match == "exact"
                and id_selectors
                and normalized_title_selectors
                and matched_ids.issuperset(id_selectors)
                and matched_titles.issuperset(normalized_title_selectors)
            ):
                break
            if title_match == "exact" and id_selectors and not normalized_title_selectors and matched_ids.issuperset(id_selectors):
                break
            if title_match == "exact" and normalized_title_selectors and not id_selectors and matched_titles.issuperset(normalized_title_selectors):
                break

            offset += len(items)
            if len(items) < page_size:
                break

    for convo_id in ids:
        convo_id = convo_id.strip()
        if not convo_id or convo_id in seen_target_ids:
            continue
        targets.append(FetchTarget(conversation_id=convo_id, title="", update_time=None))
        seen_target_ids.add(convo_id)
        if limit > 0 and len(targets) >= limit:
            break

    if debug:
        print(f"[discover] discovered targets={len(targets)}")
    return targets


def _fetch_sync(
    token: str,
    targets: list[FetchTarget],
    *,
    rate_limit_rps: float,
    extract_text: Callable[[dict[str, Any]], str],
    debug: bool,
    unsafe_allow_unverified_payload_id: bool = False,
    progress: FetchProgressReporter | None = None,
) -> list[FetchResult]:
    limiter = SyncRateLimiter(rate_limit_rps)
    results: list[FetchResult] = []

    with SyncChatGPT(session_token=token) as chatgpt:
        for idx, target in enumerate(targets, start=1):
            limiter.wait()
            started = time.monotonic()
            if progress:
                progress.start_target()
            try:
                chat = chatgpt.fetch_conversation(target.conversation_id)
                payload_id, identity_verified, identity_error = _validate_payload_identity(
                    chat,
                    target.conversation_id,
                )
                if not identity_verified and not unsafe_allow_unverified_payload_id:
                    raise ValueError(identity_error)
                parsed = _extract_messages_for_ingest(chat, extract_text=extract_text)
                asset_records = _save_asset_records(
                    _asset_artifact_inputs(parsed),
                    conversation_id=target.conversation_id,
                    downloader=lambda pointer: chatgpt.download_asset(pointer, conversation_id=target.conversation_id),
                ) if hasattr(chatgpt, "download_asset") else ()
                duration = time.monotonic() - started
                result = FetchResult(
                    conversation_id=target.conversation_id,
                    title=chat.get("title") or target.title,
                    update_time=target.update_time,
                    ok=True,
                    duration_s=duration,
                    message_count=len(parsed),
                    messages=tuple(parsed),
                    requested_conversation_id=target.conversation_id,
                    payload_conversation_id=payload_id,
                    identity_verified=identity_verified,
                    artifacts=asset_records,
                )
            except Exception as exc:  # noqa: BLE001
                duration = time.monotonic() - started
                result = FetchResult(
                    conversation_id=target.conversation_id,
                    title=target.title,
                    update_time=target.update_time,
                    ok=False,
                    duration_s=duration,
                    message_count=0,
                    error=str(exc),
                    requested_conversation_id=target.conversation_id,
                )

            results.append(result)
            if progress:
                progress.finish_target(ok=result.ok, message_count=result.message_count)
            if debug:
                status = "ok" if result.ok else "err"
                print(
                    f"[sync] {idx}/{len(targets)} id={target.conversation_id} "
                    f"status={status} msgs={result.message_count} t={result.duration_s:.2f}s"
                )

    return results


async def _fetch_async(
    token: str,
    targets: list[FetchTarget],
    *,
    concurrency: int,
    rate_limit_rps: float,
    extract_text: Callable[[dict[str, Any]], str],
    debug: bool,
    unsafe_allow_unverified_payload_id: bool = False,
    progress: FetchProgressReporter | None = None,
) -> list[FetchResult]:
    limiter = AsyncRateLimiter(rate_limit_rps)
    sem = asyncio.Semaphore(max(1, concurrency))

    async with AsyncChatGPT(session_token=token) as chatgpt:

        async def run_one(target: FetchTarget, ordinal: int) -> FetchResult:
            async with sem:
                await limiter.wait()
                started = time.monotonic()
                if progress:
                    progress.start_target()
                try:
                    chat = await chatgpt.fetch_conversation(target.conversation_id)
                    payload_id, identity_verified, identity_error = _validate_payload_identity(
                        chat,
                        target.conversation_id,
                    )
                    if not identity_verified and not unsafe_allow_unverified_payload_id:
                        raise ValueError(identity_error)
                    parsed = _extract_messages_for_ingest(chat, extract_text=extract_text)
                    asset_records = await _save_asset_records_async(
                        _asset_artifact_inputs(parsed),
                        conversation_id=target.conversation_id,
                        downloader=lambda pointer: chatgpt.download_asset(pointer, conversation_id=target.conversation_id),
                    ) if hasattr(chatgpt, "download_asset") else ()
                    result = FetchResult(
                        conversation_id=target.conversation_id,
                        title=chat.get("title") or target.title,
                        update_time=target.update_time,
                        ok=True,
                        duration_s=time.monotonic() - started,
                        message_count=len(parsed),
                        messages=tuple(parsed),
                        requested_conversation_id=target.conversation_id,
                        payload_conversation_id=payload_id,
                        identity_verified=identity_verified,
                        artifacts=asset_records,
                    )
                except Exception as exc:  # noqa: BLE001
                    result = FetchResult(
                        conversation_id=target.conversation_id,
                        title=target.title,
                        update_time=target.update_time,
                        ok=False,
                        duration_s=time.monotonic() - started,
                        message_count=0,
                        error=str(exc),
                        requested_conversation_id=target.conversation_id,
                    )

                if progress:
                    progress.finish_target(ok=result.ok, message_count=result.message_count)
                if debug:
                    status = "ok" if result.ok else "err"
                    print(
                        f"[async] {ordinal}/{len(targets)} id={target.conversation_id} "
                        f"status={status} msgs={result.message_count} t={result.duration_s:.2f}s"
                    )
                return result

        tasks = [asyncio.create_task(run_one(target, idx + 1)) for idx, target in enumerate(targets)]
        return list(await asyncio.gather(*tasks))


def _flatten_for_ingest(
    results: Iterable[FetchResult],
    *,
    unsafe_allow_unverified_payload_id: bool = False,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in results:
        if not result.ok:
            continue
        if not result.identity_verified and not unsafe_allow_unverified_payload_id:
            raise ValueError(
                "Refusing to flatten unverified payload identity for "
                f"{result.requested_conversation_id or result.conversation_id}."
            )
        thread_id = result.payload_conversation_id
        if not thread_id:
            if not unsafe_allow_unverified_payload_id:
                raise ValueError(
                    "Refusing to flatten payload with missing conversation identity for "
                    f"{result.requested_conversation_id or result.conversation_id}."
                )
            thread_id = result.requested_conversation_id or result.conversation_id
        for msg in result.messages:
            rec = dict(msg)
            rec["thread_id"] = thread_id
            rec["thread_title"] = result.title
            rec["requested_conversation_id"] = result.requested_conversation_id or result.conversation_id
            rec["payload_conversation_id"] = result.payload_conversation_id
            rec["identity_verified"] = result.identity_verified
            rec["provenance_json"] = json.dumps(
                {
                    "chatgpt_payload_identity": {
                        "requested_conversation_id": result.requested_conversation_id or result.conversation_id,
                        "payload_conversation_id": result.payload_conversation_id,
                        "identity_verified": result.identity_verified,
                    }
                },
                sort_keys=True,
            )
            out.append(rec)
    return out


def _artifacts_for_ingest(results: Iterable[FetchResult]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if not result.ok or not result.payload_conversation_id:
            continue
        if result.artifacts:
            output[result.payload_conversation_id] = [dict(item) for item in result.artifacts]
    return output


def _summarize(results: list[FetchResult], duration_s: float) -> dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    failures = total - ok
    msg_count = sum(r.message_count for r in results if r.ok)
    elapsed = max(0.001, duration_s)
    return {
        "total": total,
        "ok": ok,
        "failures": failures,
        "messages": msg_count,
        "duration_s": round(duration_s, 3),
        "conv_per_s": round(ok / elapsed, 3),
        "msg_per_s": round(msg_count / elapsed, 3),
    }


def _run_engine(
    engine: str,
    token: str,
    targets: list[FetchTarget],
    *,
    concurrency: int,
    rate_limit_rps: float,
    extract_text: Callable[[dict[str, Any]], str],
    debug: bool,
    progress_enabled: bool = True,
    unsafe_allow_unverified_payload_id: bool = False,
) -> tuple[list[FetchResult], float]:
    started = time.monotonic()
    progress = FetchProgressReporter(total=len(targets), engine=engine, enabled=progress_enabled)
    if engine == "sync":
        results = _fetch_sync(
            token,
            targets,
            rate_limit_rps=rate_limit_rps,
            extract_text=extract_text,
            debug=debug,
            unsafe_allow_unverified_payload_id=unsafe_allow_unverified_payload_id,
            progress=progress,
        )
    else:
        results = asyncio.run(
            _fetch_async(
                token,
                targets,
                concurrency=concurrency,
                rate_limit_rps=rate_limit_rps,
                extract_text=extract_text,
                debug=debug,
                unsafe_allow_unverified_payload_id=unsafe_allow_unverified_payload_id,
                progress=progress,
            )
        )
    progress.finish()
    return results, time.monotonic() - started


def _parse_selectors(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    titles: list[str] = []

    if args.ids:
        ids.extend(_parse_csv_values(args.ids))
    if args.ids_file:
        ids.extend(_parse_value_file(args.ids_file))
    if args.titles:
        titles.extend(_parse_csv_values(args.titles))
    if args.titles_file:
        titles.extend(_parse_value_file(args.titles_file))
    selector_ids, selector_titles = _parse_selector_file(args.selectors_file)
    ids.extend(selector_ids)
    titles.extend(selector_titles)

    deduped_ids: list[str] = []
    seen: set[str] = set()
    for item in ids:
        if item not in seen:
            seen.add(item)
            deduped_ids.append(item)

    deduped_titles: list[str] = []
    seen_titles: set[str] = set()
    for item in titles:
        normalized = _normalize_title(item)
        if not normalized or normalized in seen_titles:
            continue
        seen_titles.add(normalized)
        deduped_titles.append(item.strip())

    return deduped_ids, deduped_titles


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="Session token (defaults to ~/.chatgpt_session or config)")
    parser.add_argument("--db", default=str(Path.home() / "chat_archive.sqlite"))
    parser.add_argument("--account", default="main")
    parser.add_argument("--source-id", help="Source ID for ingestion (default: auto UTC timestamp)")
    parser.add_argument("--mode", choices=("pull", "bench"), default="pull")
    parser.add_argument("--engine", choices=("sync", "async"), default="async")
    parser.add_argument("--limit", type=int, default=50, help="How many conversations to fetch when IDs are not provided")
    parser.add_argument("--page-size", type=int, default=28)
    parser.add_argument("--max-pages", type=int, help="Optional cap on catalog pages to scan during discovery")
    parser.add_argument("--ids", help="Comma-separated conversation IDs")
    parser.add_argument("--ids-file", help="File containing one conversation ID per line")
    parser.add_argument("--titles", help="Comma-separated conversation titles/selectors")
    parser.add_argument("--titles-file", help="File containing one title selector per line")
    parser.add_argument(
        "--selectors-file",
        help=(
            "File with mixed selectors (lines can be raw ID/title, or prefixed with "
            "'id:' / 'title:')."
        ),
    )
    parser.add_argument(
        "--title-match",
        choices=("exact", "contains"),
        default="exact",
        help="How title selectors are matched against live titles (default: exact).",
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rate-limit-rps", type=float, default=3.0)
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only; do not ingest")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    parser.add_argument("--no-progress", action="store_true", help="Suppress stderr fetch progress updates")
    parser.add_argument(
        "--unsafe-allow-unverified-payload-id",
        action="store_true",
        help=(
            "Emergency override: ingest fetched payloads even when the response "
            "does not prove its conversation id. Records unverified provenance."
        ),
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.no_skip_existing:
        args.skip_existing = False

    token = args.token or get_session_token()
    db_path = Path(args.db).expanduser()
    source_id = args.source_id or f"pull_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    existing = _load_existing_updates(db_path, account_id=args.account) if args.skip_existing else {}
    ids, titles = _parse_selectors(args)
    targets = _discover_targets(
        token,
        ids=ids,
        titles=titles,
        title_match=args.title_match,
        limit=max(0, args.limit),
        page_size=max(1, args.page_size),
        max_pages=args.max_pages,
        skip_existing=args.skip_existing,
        existing_updates=existing,
        debug=args.debug,
    )

    if not targets:
        payload = {
            "mode": args.mode,
            "engine": args.engine,
            "targets": 0,
            "message": "no targets to fetch",
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("No targets to fetch.")
        return 0

    extract_text = _load_chatgpt_parser()

    summaries: dict[str, Any] = {
        "mode": args.mode,
        "requested_targets": len(targets),
        "source_id": source_id,
        "db": str(db_path),
        "selected_id_filters": len(ids),
        "selected_title_filters": len(titles),
        "title_match": args.title_match,
        "max_pages": args.max_pages,
    }

    if args.mode == "bench":
        sync_results, sync_elapsed = _run_engine(
            "sync",
            token,
            targets,
            concurrency=max(1, args.concurrency),
            rate_limit_rps=args.rate_limit_rps,
            extract_text=extract_text,
            debug=args.debug,
            progress_enabled=not args.no_progress,
            unsafe_allow_unverified_payload_id=args.unsafe_allow_unverified_payload_id,
        )
        async_results, async_elapsed = _run_engine(
            "async",
            token,
            targets,
            concurrency=max(1, args.concurrency),
            rate_limit_rps=args.rate_limit_rps,
            extract_text=extract_text,
            debug=args.debug,
            progress_enabled=not args.no_progress,
            unsafe_allow_unverified_payload_id=args.unsafe_allow_unverified_payload_id,
        )

        summaries["sync"] = _summarize(sync_results, sync_elapsed)
        summaries["async"] = _summarize(async_results, async_elapsed)

        # Optional ingest using async results in benchmark mode.
        if not args.dry_run:
            ingest_module = _load_structurer_ingest()
            normalized = _flatten_for_ingest(
                async_results,
                unsafe_allow_unverified_payload_id=args.unsafe_allow_unverified_payload_id,
            )
            ingest_stats = ingest_module.ingest_parsed_messages(
                normalized,
                db_path=str(db_path),
                platform="chatgpt",
                account_id=args.account,
                source_id=source_id,
                upsert_empty_text=True,
                debug=args.debug,
            )
            artifact_stats = ingest_module.ingest_thread_artifacts(
                db_path=str(db_path), platform="chatgpt", account_id=args.account,
                artifacts_by_thread=_artifacts_for_ingest(async_results),
            )
            ingest_stats["artifact_index"] = artifact_stats
            summaries["ingest"] = ingest_stats
    else:
        results, elapsed = _run_engine(
            args.engine,
            token,
            targets,
            concurrency=max(1, args.concurrency),
            rate_limit_rps=args.rate_limit_rps,
            extract_text=extract_text,
            debug=args.debug,
            progress_enabled=not args.no_progress,
            unsafe_allow_unverified_payload_id=args.unsafe_allow_unverified_payload_id,
        )
        summaries[args.engine] = _summarize(results, elapsed)

        if not args.dry_run:
            ingest_module = _load_structurer_ingest()
            normalized = _flatten_for_ingest(
                results,
                unsafe_allow_unverified_payload_id=args.unsafe_allow_unverified_payload_id,
            )
            ingest_stats = ingest_module.ingest_parsed_messages(
                normalized,
                db_path=str(db_path),
                platform="chatgpt",
                account_id=args.account,
                source_id=source_id,
                upsert_empty_text=True,
                debug=args.debug,
            )
            artifact_stats = ingest_module.ingest_thread_artifacts(
                db_path=str(db_path), platform="chatgpt", account_id=args.account,
                artifacts_by_thread=_artifacts_for_ingest(results),
            )
            ingest_stats["artifact_index"] = artifact_stats
            summaries["ingest"] = ingest_stats

    if args.json:
        print(json.dumps(summaries, indent=2, sort_keys=True))
    else:
        print("Summary:")
        for key, value in summaries.items():
            if isinstance(value, dict):
                print(f"  {key}: {value}")
            else:
                print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
