"""Persistence helpers for ChatGPT conversations."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

DEFAULT_DB_PATH = Path.home() / ".chatgpt_history.sqlite3"
DEFAULT_EXPORT_DIR = Path("chat_exports")


@dataclass
class PersistResult:
    """Result details after persisting a conversation."""

    json_path: Path
    new_messages: int
    total_messages: int
    asset_paths: Tuple[Path, ...] = ()
    asset_errors: Tuple[str, ...] = ()


@dataclass
class AssetDownload:
    """Binary payload fetched for an asset pointer."""

    content: bytes
    content_type: Optional[str] = None


@dataclass
class ImageAsset:
    """Descriptor for an image discovered inside a conversation payload."""

    pointer: str
    file_id: str
    mime_type: Optional[str] = None
    extension: Optional[str] = None
    size_hint: Optional[int] = None


@dataclass
class CatalogUpdateStats:
    """Track how many conversation records were added or refreshed."""

    added: int = 0
    updated: int = 0


def extract_ordered_messages(chat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract and order messages from a conversation mapping."""

    messages: list[dict[str, Any]] = []

    mapping = chat.get("mapping") if isinstance(chat, Mapping) else None
    if not isinstance(mapping, Mapping):
        return messages

    for node in mapping.values():
        if not isinstance(node, Mapping):
            continue

        message = node.get("message")
        if not isinstance(message, Mapping):
            continue

        author_info = message.get("author")
        author = ""
        if isinstance(author_info, Mapping):
            author = str(author_info.get("role") or "")

        content_info = message.get("content")
        parts: list[str] = []
        if isinstance(content_info, Mapping):
            raw_parts = content_info.get("parts") or []
            if isinstance(raw_parts, Iterable) and not isinstance(raw_parts, (str, bytes)):
                for part in raw_parts:
                    text = ""
                    if isinstance(part, str):
                        text = part.strip()
                    elif isinstance(part, Mapping):
                        for key in ("text", "content", "title"):
                            value = part.get(key)
                            if value:
                                text = str(value).strip()
                                break
                    if text:
                        parts.append(text)

        if not parts:
            continue

        create_time = message.get("create_time")
        if not isinstance(create_time, (int, float)):
            create_time = 0

        messages.append(
            {
                "author": author,
                "content": "\n".join(parts),
                "create_time": create_time,
            }
        )

    messages.sort(key=lambda item: item.get("create_time", 0))
    for index, message in enumerate(messages):
        message["message_index"] = index
    return messages


class ConversationStorage:
    """Persist conversations to JSON files and a SQLite database."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        export_dir: Path | str = DEFAULT_EXPORT_DIR,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.export_dir = Path(export_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.execute("PRAGMA journal_mode=WAL;")
        self._connection.execute("PRAGMA foreign_keys=ON;")
        self._initialise_schema()
        self._conversation_key_cache: dict[str, str] = {}
        self._message_table_columns = self._column_names("messages")
        self._has_message_key_column = "message_key" in self._message_table_columns
        self._conversation_table_columns = self._column_names("conversations")
        self._backfill_conversation_metadata()

    def __enter__(self) -> "ConversationStorage":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._connection.close()

    def _initialise_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    conversation_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    create_time REAL,
                    message_key TEXT,
                    PRIMARY KEY (conversation_id, message_index, author)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    internal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL UNIQUE,
                    conversation_key TEXT NOT NULL,
                    title TEXT,
                    discovered_at REAL NOT NULL DEFAULT 0,
                    last_seen_at REAL NOT NULL DEFAULT 0,
                    remote_update_time REAL,
                    cached_message_count INTEGER DEFAULT 0
                )
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_key
                ON conversations(conversation_key)
                """
            )

        self._ensure_column(
            "messages",
            "message_key",
            "ALTER TABLE messages ADD COLUMN message_key TEXT",
        )
        self._ensure_column(
            "conversations",
            "conversation_key",
            "ALTER TABLE conversations ADD COLUMN conversation_key TEXT DEFAULT ''",
        )
        self._ensure_column(
            "conversations",
            "discovered_at",
            "ALTER TABLE conversations ADD COLUMN discovered_at REAL DEFAULT 0",
        )
        self._ensure_column(
            "conversations",
            "last_seen_at",
            "ALTER TABLE conversations ADD COLUMN last_seen_at REAL DEFAULT 0",
        )
        self._ensure_column(
            "conversations",
            "remote_update_time",
            "ALTER TABLE conversations ADD COLUMN remote_update_time REAL",
        )
        self._ensure_column(
            "conversations",
            "cached_message_count",
            "ALTER TABLE conversations ADD COLUMN cached_message_count INTEGER DEFAULT 0",
        )

    def _column_names(self, table: str) -> set[str]:
        cursor = self._connection.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        if column not in self._column_names(table):
            with self._connection:
                self._connection.execute(ddl)

    def get_conversation_key(self, conversation_id: str) -> Optional[str]:
        if conversation_id in self._conversation_key_cache:
            return self._conversation_key_cache[conversation_id]

        cursor = self._connection.execute(
            "SELECT conversation_key FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            key = str(row[0])
            self._conversation_key_cache[conversation_id] = key
            return key
        return None

    def _get_existing_conversation_ids(self) -> set[str]:
        cursor = self._connection.execute("SELECT conversation_id FROM conversations")
        return {str(row[0]) for row in cursor.fetchall() if row[0]}

    def _backfill_conversation_metadata(self) -> None:
        if "conversation_id" not in self._conversation_table_columns:
            return

        now = time.time()
        cursor = self._connection.execute(
            """
            SELECT conversation_id, title, conversation_key, discovered_at, last_seen_at
            FROM conversations
            """
        )
        rows = cursor.fetchall()

        with self._connection:
            for conversation_id, title, stored_key, discovered_at, last_seen_at in rows:
                if not conversation_id:
                    continue
                computed_key = self.compute_conversation_key(conversation_id, title)
                key = stored_key or computed_key
                discovered = discovered_at or now
                seen = last_seen_at or discovered
                if key != stored_key or discovered_at != discovered or last_seen_at != seen:
                    self._connection.execute(
                        """
                        UPDATE conversations
                        SET conversation_key = ?, discovered_at = ?, last_seen_at = ?
                        WHERE conversation_id = ?
                        """,
                        (key, discovered, seen, conversation_id),
                    )
                self._conversation_key_cache[conversation_id] = key

    @staticmethod
    def compute_conversation_key(conversation_id: str, title: Optional[str] = None) -> str:
        """Generate a stable key based on ``conversation_id`` and optional ``title``."""

        base = conversation_id or (title or "")
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
        return digest[:16]

    @staticmethod
    def build_message_key(conversation_key: str, author: str, index: int) -> str:
        """Compose a message identifier for the stored message."""

        safe_author = author or "unknown"
        return f"{conversation_key}.{safe_author}.{index:04d}"

    def ensure_conversation_record(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        remote_update_time: Optional[float] = None,
    ) -> str:
        """Ensure a row exists in ``conversations`` and return its key."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        now = time.time()
        computed_key = self.compute_conversation_key(conversation_id, title)

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id,
                    conversation_key,
                    title,
                    discovered_at,
                    last_seen_at,
                    remote_update_time,
                    cached_message_count
                ) VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    title = CASE
                        WHEN excluded.title IS NOT NULL AND excluded.title != ''
                        THEN excluded.title
                        ELSE conversations.title
                    END,
                    conversation_key = CASE
                        WHEN conversations.conversation_key IS NULL
                             OR conversations.conversation_key = ''
                        THEN excluded.conversation_key
                        ELSE conversations.conversation_key
                    END,
                    last_seen_at = excluded.last_seen_at,
                    remote_update_time = CASE
                        WHEN excluded.remote_update_time IS NOT NULL
                             AND (
                                conversations.remote_update_time IS NULL
                                OR excluded.remote_update_time > conversations.remote_update_time
                             )
                        THEN excluded.remote_update_time
                        ELSE conversations.remote_update_time
                    END
                """,
                (
                    conversation_id,
                    computed_key,
                    title,
                    now,
                    now,
                    remote_update_time,
                ),
            )

        stored_key = self.get_conversation_key(conversation_id) or computed_key
        self._conversation_key_cache[conversation_id] = stored_key
        return stored_key

    def record_conversations(self, conversations: Sequence[Mapping[str, Any]]) -> CatalogUpdateStats:
        """Persist a batch of conversation headers, returning update stats."""

        stats = CatalogUpdateStats()
        if not conversations:
            return stats

        existing_ids = self._get_existing_conversation_ids()
        for entry in conversations:
            conv_id = str(
                entry.get("id")
                or entry.get("conversation_id")
                or ""
            ).strip()
            if not conv_id:
                continue

            title = entry.get("title")
            remote_update = (
                entry.get("last_updated")
                or entry.get("update_time")
            )

            is_new = conv_id not in existing_ids
            self.ensure_conversation_record(conv_id, title, remote_update_time=remote_update)
            if is_new:
                stats.added += 1
                existing_ids.add(conv_id)
            else:
                stats.updated += 1

        return stats

    def persist_chat(
        self,
        conversation_id: str,
        chat: Mapping[str, Any],
        messages: Iterable[Mapping[str, Any]] | None = None,
        asset_fetcher: Optional[Callable[[str], AssetDownload]] = None,
    ) -> PersistResult:
        """Persist a conversation to JSON and the database."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        if messages is None:
            messages = extract_ordered_messages(chat)

        title: Optional[str] = None
        remote_update: Optional[float] = None
        if isinstance(chat, Mapping):
            title = chat.get("title")
            remote_update = chat.get("update_time") or chat.get("last_updated")

        conversation_key = self.ensure_conversation_record(
            conversation_id,
            title=title,
            remote_update_time=remote_update,
        )
        export_basename = self._build_export_basename(
            conversation_id,
            title=title,
            conversation_key=conversation_key,
        )
        new_messages, total_messages = self.upsert_messages(
            conversation_id,
            messages,
            conversation_key=conversation_key,
        )
        self.update_cached_message_count(conversation_id, total_messages)
        json_path = self.export_conversation(export_basename, chat)

        asset_paths: list[Path] = []
        asset_errors: list[str] = []
        if asset_fetcher:
            discovered_assets = self._collect_image_assets(chat)
            if discovered_assets:
                downloaded, failures = self._download_image_assets(
                    export_basename,
                    discovered_assets,
                    asset_fetcher,
                )
                asset_paths.extend(downloaded)
                asset_errors.extend(failures)

        return PersistResult(
            json_path=json_path,
            new_messages=new_messages,
            total_messages=total_messages,
            asset_paths=tuple(asset_paths),
            asset_errors=tuple(asset_errors),
        )

    def export_conversation(self, export_basename: str, chat: Mapping[str, Any]) -> Path:
        """Write ``chat`` to a JSON file in ``export_dir``."""

        if not export_basename:
            raise ValueError("export_basename must be provided")

        self.export_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.export_dir / f"{export_basename}.json"
        with json_path.open("w", encoding="utf-8") as handle:
            json.dump(chat, handle, indent=2, ensure_ascii=False)
        return json_path

    def _build_export_basename(
        self,
        conversation_id: str,
        title: Optional[str],
        conversation_key: Optional[str],
    ) -> str:
        slug = self._slugify_title(title)
        unique_token = (conversation_key or conversation_id or "").strip()
        unique_token = self._safe_token(unique_token) or self.compute_conversation_key(conversation_id, title)
        if slug:
            return f"{slug}__{unique_token}"
        return f"conversation__{unique_token}"

    @staticmethod
    def _slugify_title(title: Optional[str]) -> str:
        if not title:
            return ""
        normalized = unicodedata.normalize("NFKD", title)
        ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_title = ascii_title.lower()
        ascii_title = re.sub(r"[^a-z0-9]+", "-", ascii_title)
        ascii_title = re.sub(r"-{2,}", "-", ascii_title)
        ascii_title = ascii_title.strip("-")
        if len(ascii_title) > 80:
            ascii_title = ascii_title[:80].rstrip("-")
        return ascii_title

    @staticmethod
    def _safe_token(token: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", token)
        cleaned = cleaned.strip("-")
        if len(cleaned) > 24:
            cleaned = cleaned[:24].rstrip("-")
        return cleaned

    def _collect_image_assets(self, chat: Mapping[str, Any]) -> list[ImageAsset]:
        mapping = chat.get("mapping")
        if not isinstance(mapping, Mapping):
            return []

        collected: dict[str, ImageAsset] = {}
        allowed_pointer_schemes = {"file-service", "fileservice", "file", "sediment"}

        def normalise_pointer(value: Optional[str]) -> Optional[str]:
            if not value or not isinstance(value, str):
                return None
            stripped = value.strip()
            if "://" not in stripped:
                return None
            scheme, remainder = stripped.split("://", 1)
            scheme = scheme.strip().lower()
            remainder = remainder.strip()
            if not remainder:
                return None
            if scheme in {"fileservice", "file"}:
                scheme = "file-service"
            if scheme not in allowed_pointer_schemes:
                return None
            return f"{scheme}://{remainder}"

        def register(
            pointer: Optional[str],
            *,
            mime: Optional[str] = None,
            extension: Optional[str] = None,
            size: Optional[int] = None,
        ) -> None:
            pointer = normalise_pointer(pointer)
            if not pointer:
                return

            file_id = self._extract_file_id(pointer)
            if not file_id:
                return

            asset = collected.get(pointer)
            if asset is None:
                asset = ImageAsset(pointer=pointer, file_id=file_id)
                collected[pointer] = asset

            scheme = pointer.split("://", 1)[0].lower()

            if mime and not asset.mime_type:
                asset.mime_type = mime
            if extension and not asset.extension and isinstance(extension, str):
                asset.extension = extension.lstrip(".").lower()
            if scheme == "sediment":
                if not asset.mime_type:
                    asset.mime_type = "image/png"
                if not asset.extension:
                    asset.extension = "png"
            if size and not asset.size_hint:
                try:
                    asset.size_hint = int(size)
                except (TypeError, ValueError):
                    pass

        def traverse(node: Any, key_hint: Optional[str] = None) -> None:
            if isinstance(node, Mapping):
                asset_pointer = node.get("asset_pointer")
                if isinstance(asset_pointer, str):
                    register(
                        asset_pointer,
                        mime=self._mime_from_key(key_hint),
                        extension=self._extension_from_key(key_hint),
                        size=node.get("size_bytes") or node.get("bytes") or node.get("size"),
                    )
                for key, value in node.items():
                    traverse(value, key if isinstance(key, str) else key_hint)
                return

            if isinstance(node, list):
                for item in node:
                    traverse(item, key_hint)
                return

            if isinstance(node, str):
                stripped = node.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        pass
                    else:
                        traverse(payload, key_hint)
                        return
                candidate = normalise_pointer(stripped)
                if candidate:
                    register(
                        candidate,
                        mime=self._mime_from_key(key_hint),
                        extension=self._extension_from_key(key_hint),
                    )
                    return
                for match in re.finditer(
                    r"(?:file-service|fileservice|sediment)://[A-Za-z0-9._-]+",
                    node,
                    flags=re.IGNORECASE,
                ):
                    candidate = normalise_pointer(match.group(0))
                    if candidate:
                        register(
                            candidate,
                            mime=self._mime_from_key(key_hint),
                            extension=self._extension_from_key(key_hint),
                        )

        for node in mapping.values():
            if not isinstance(node, Mapping):
                continue
            message = node.get("message")
            if not isinstance(message, Mapping):
                continue

            metadata = message.get("metadata") or {}
            aggregate = metadata.get("aggregate_result")
            if isinstance(aggregate, Mapping):
                messages = aggregate.get("messages") or []
                if isinstance(messages, Iterable) and not isinstance(messages, (str, bytes)):
                    for entry in messages:
                        if not isinstance(entry, Mapping):
                            continue
                        pointer = entry.get("image_url")
                        register(
                            pointer,
                            mime="image/png",
                            extension="png",
                            size=entry.get("size_bytes") or entry.get("bytes"),
                        )
                jupyter_messages = aggregate.get("jupyter_messages") or []
                if isinstance(jupyter_messages, Iterable) and not isinstance(jupyter_messages, (str, bytes)):
                    for jupyter in jupyter_messages:
                        if not isinstance(jupyter, Mapping):
                            continue
                        content = jupyter.get("content") or {}
                        data = content.get("data") or {}
                        if not isinstance(data, Mapping):
                            continue
                        for key, value in data.items():
                            traverse(
                                value,
                                key if isinstance(key, str) else None,
                            )

            content = message.get("content")
            if content:
                traverse(content)

            meta_attachments = metadata.get("attachments")
            if isinstance(meta_attachments, Iterable) and not isinstance(meta_attachments, (str, bytes)):
                for attachment in meta_attachments:
                    if not isinstance(attachment, Mapping):
                        continue
                    pointer = attachment.get("asset_pointer")
                    if not pointer:
                        attachment_id = attachment.get("id") or attachment.get("file_id")
                        if isinstance(attachment_id, str):
                            pointer = f"file-service://{attachment_id}"
                    register(
                        pointer,
                        mime=attachment.get("mime_type"),
                        extension=self._extension_from_mime(attachment.get("mime_type")),
                        size=attachment.get("size") or attachment.get("bytes"),
                    )

            attachments = message.get("attachments")
            if attachments:
                traverse(attachments)

        return list(collected.values())

    def _download_image_assets(
        self,
        export_basename: str,
        assets: Sequence[ImageAsset],
        asset_fetcher: Callable[[str], AssetDownload],
    ) -> Tuple[list[Path], list[str]]:
        assets_dir = self.export_dir / f"{export_basename}_files"
        assets_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[Path] = []
        failures: list[str] = []
        processed: set[str] = set()

        for asset in assets:
            pointer = asset.pointer
            if pointer in processed:
                continue
            processed.add(pointer)

            extension = (
                asset.extension
                or self._extension_from_mime(asset.mime_type)
                or "bin"
            )
            extension = extension.lstrip(".")

            filename = self._safe_filename(asset.file_id)
            if extension:
                filename = f"{filename}.{extension}"

            file_path = assets_dir / filename

            if file_path.exists():
                saved_paths.append(file_path)
                continue

            try:
                download = asset_fetcher(pointer)
            except Exception as exc:  # noqa: BLE001 - surface asset failures to caller.
                failures.append(f"{pointer}: {exc}")
                continue

            content_type = getattr(download, "content_type", None)
            if (extension == "bin" or not extension) and content_type:
                inferred = self._extension_from_mime(content_type)
                if inferred:
                    new_name = file_path.with_suffix(f".{inferred}")
                    file_path = new_name

            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("wb") as handle:
                handle.write(download.content)

            saved_paths.append(file_path)

        return saved_paths, failures

    @staticmethod
    def _extract_file_id(pointer: str) -> str:
        if "://" not in pointer:
            return ""
        remainder = pointer.split("://", 1)[1]
        remainder = remainder.strip("/")
        return remainder

    @staticmethod
    def _mime_from_key(key_hint: Optional[str]) -> Optional[str]:
        if not key_hint or not isinstance(key_hint, str):
            return None
        candidate = key_hint.split(";")[0]
        if candidate.startswith("image/"):
            return candidate
        return None

    @staticmethod
    def _extension_from_mime(mime: Optional[str]) -> Optional[str]:
        if not mime:
            return None
        ext = mimetypes.guess_extension(mime)
        if ext:
            return ext.lstrip(".")
        return None

    @staticmethod
    def _extension_from_key(key_hint: Optional[str]) -> Optional[str]:
        if not key_hint or not isinstance(key_hint, str):
            return None
        mime_ext = mimetypes.guess_extension(key_hint)
        if mime_ext:
            return mime_ext.lstrip(".")
        if "." in key_hint:
            candidate = key_hint.rsplit(".", 1)[1]
            candidate = candidate.split(";")[0]
            candidate = candidate.split("+")[0]
            candidate = candidate.lower()
            if 1 <= len(candidate) <= 5 and candidate.isalnum():
                return candidate
        return None

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        cleaned = cleaned.strip("._-")
        return cleaned or "asset"

    def upsert_messages(
        self,
        conversation_id: str,
        messages: Iterable[Mapping[str, Any]],
        conversation_key: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Insert or update messages for ``conversation_id``."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        existing_rows = self._connection.execute(
            """
            SELECT message_index, author
            FROM messages
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchall()
        existing_keys = {
            (int(row[0]), str(row[1] or ""))
            for row in existing_rows
        }

        if conversation_key is None:
            conversation_key = self.get_conversation_key(conversation_id)
        if conversation_key is None:
            conversation_key = self.ensure_conversation_record(conversation_id)

        new_messages = 0

        insert_sql_with_key = """
            INSERT INTO messages (
                conversation_id,
                message_index,
                author,
                content,
                create_time,
                message_key
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, message_index, author)
            DO UPDATE SET
                content = excluded.content,
                create_time = excluded.create_time,
                message_key = excluded.message_key
        """
        insert_sql_without_key = """
            INSERT INTO messages (
                conversation_id,
                message_index,
                author,
                content,
                create_time
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, message_index, author)
            DO UPDATE SET
                content = excluded.content,
                create_time = excluded.create_time
        """
        sql = insert_sql_with_key if self._has_message_key_column else insert_sql_without_key

        with self._connection:
            for message in messages:
                index = int(message.get("message_index", 0))
                author = str(message.get("author") or "")
                content = str(message.get("content") or "")
                create_time = message.get("create_time")
                key_tuple = (index, author)
                is_new = key_tuple not in existing_keys
                if is_new:
                    new_messages += 1
                    existing_keys.add(key_tuple)

                if self._has_message_key_column:
                    message_key = self.build_message_key(conversation_key, author, index)
                    params = (
                        conversation_id,
                        index,
                        author,
                        content,
                        create_time,
                        message_key,
                    )
                else:
                    params = (
                        conversation_id,
                        index,
                        author,
                        content,
                        create_time,
                    )
                self._connection.execute(sql, params)

        total_messages = len(existing_keys)
        return new_messages, total_messages

    def update_cached_message_count(self, conversation_id: str, message_count: int) -> None:
        """Record the current cached message count for a conversation."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        now = time.time()
        with self._connection:
            self._connection.execute(
                """
                UPDATE conversations
                SET cached_message_count = ?, last_seen_at = ?
                WHERE conversation_id = ?
                """,
                (message_count, now, conversation_id),
            )

    def count_messages(self, conversation_id: str) -> int:
        """Return the number of messages cached locally for a conversation."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        cursor = self._connection.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def append_message(
        self,
        conversation_id: str,
        author: str,
        content: str,
        create_time: float | None = None,
    ) -> int:
        """Append a message and return its message index."""

        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        conversation_key = self.ensure_conversation_record(conversation_id)
        if create_time is None:
            create_time = time.time()

        with self._connection:
            cursor = self._connection.execute(
                "SELECT COALESCE(MAX(message_index), -1) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            next_index = int(cursor.fetchone()[0]) + 1
            if self._has_message_key_column:
                message_key = self.build_message_key(conversation_key, author or "", next_index)
                sql = """
                    INSERT INTO messages (
                        conversation_id,
                        message_index,
                        author,
                        content,
                        create_time,
                        message_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, message_index, author)
                    DO UPDATE SET
                        content = excluded.content,
                        create_time = excluded.create_time,
                        message_key = excluded.message_key
                """
                params = (
                    conversation_id,
                    next_index,
                    author,
                    content,
                    create_time,
                    message_key,
                )
            else:
                sql = """
                    INSERT INTO messages (
                        conversation_id,
                        message_index,
                        author,
                        content,
                        create_time
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, message_index, author)
                    DO UPDATE SET
                        content = excluded.content,
                        create_time = excluded.create_time
                """
                params = (
                    conversation_id,
                    next_index,
                    author,
                    content,
                    create_time,
                )
            self._connection.execute(sql, params)

        self.update_cached_message_count(conversation_id, next_index + 1)
        return next_index
