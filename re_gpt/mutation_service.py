"""Reusable browser-owned mutation service.

Browser evidence makes a second trigger unsafe; only a validated conversation
graph certifies commit. Direct-client rejection stops that context immediately;
reconciliation may fall back to an authenticated browser same-origin GET but
never resubmits the mutation.
"""

from __future__ import annotations

import functools
import time
from pathlib import Path
from typing import Any, Sequence

from .attachment_archive import AttachmentArchive
from .attachments import LocalAttachment, match_expected_attachments
from .browser_observer import fetch_conversation_same_origin
from .browser_write import BrowserWriteError, submit_prompt
from .mutation_receipt import classify_prompt_mutation
from .operation_store import MutationOperationStore
from .read_plane import fetch_validated_conversation, install_validated_sync_read_plane
from .storage import ConversationStorage, DEFAULT_DB_PATH, extract_ordered_messages
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token

install_validated_sync_read_plane()


class DirectClientRejected(RuntimeError):
    """The direct exact-thread HTTP context was rejected; do not retry it blindly."""


def _assistant_child_id(chat: dict[str, Any], user_node_id: str) -> str | None:
    mapping = chat.get("mapping")
    if not isinstance(mapping, dict):
        return None
    candidates: list[tuple[float, str]] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict) or str(node.get("parent") or "") != user_node_id:
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        if not isinstance(author, dict) or author.get("role") != "assistant":
            continue
        try:
            created = float(message.get("create_time") or message.get("update_time") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        candidates.append((created, str(node_id)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _is_direct_client_rejection(exc: BaseException) -> bool:
    if isinstance(exc, DirectClientRejected):
        return True
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


def _stored_expected_attachments(op: dict[str, Any]) -> list[LocalAttachment]:
    expected: list[LocalAttachment] = []
    for row in op.get("attachments") or []:
        try:
            expected.append(LocalAttachment(
                path=Path(str(row["source_path"])),
                filename=str(row["filename"]),
                mime_type=str(row["mime_type"]) if row.get("mime_type") else None,
                size=int(row["size_bytes"]),
                sha256=str(row["sha256"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return expected


class MutationService:
    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        session_token: str | None = None,
        model: str | None = None,
        export_json: bool = False,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.session_token = session_token
        self.model = model
        self.export_json = export_json

    def operation_status(self, operation_id: str) -> dict[str, Any] | None:
        with MutationOperationStore(self.db_path) as operations:
            return operations.get(operation_id)

    def _persist_chat(
        self,
        conversation_id: str,
        chat: dict[str, Any],
        *,
        asset_fetcher=None,
        read_adapter: str,
        read_diagnostic: dict[str, Any] | None = None,
        direct_client_rejected: bool = False,
    ) -> dict[str, Any]:
        messages = extract_ordered_messages(chat)
        with ConversationStorage(db_path=self.db_path, write_json=self.export_json) as storage:
            persisted = storage.persist_chat(
                conversation_id,
                chat,
                messages,
                asset_fetcher=asset_fetcher,
            )
        with AttachmentArchive(self.db_path) as attachment_archive:
            canonical_attachment_count = attachment_archive.persist_graph(conversation_id, chat)
        return {
            "chat": chat,
            "new_messages": persisted.new_messages,
            "total_messages": persisted.total_messages,
            "json_path": str(persisted.json_path) if persisted.json_path else None,
            "asset_count": len(persisted.asset_paths),
            "asset_errors": len(persisted.asset_errors),
            "canonical_attachment_count": canonical_attachment_count,
            "read_adapter": read_adapter,
            "read_diagnostic": read_diagnostic or {},
            "direct_client_rejected": direct_client_rejected,
        }

    def _pull_sync_and_persist(self, conversation_id: str) -> dict[str, Any]:
        token = self.session_token or get_session_token()
        default_model = self.model or get_default_model()
        user_agent = get_default_user_agent()
        with SyncChatGPT(
            session_token=token,
            default_model=default_model,
            user_agent=user_agent,
        ) as chatgpt:
            chat = fetch_validated_conversation(chatgpt, conversation_id)
            asset_fetcher = None
            if hasattr(chatgpt, "download_asset"):
                asset_fetcher = functools.partial(chatgpt.download_asset, conversation_id=conversation_id)
            return self._persist_chat(
                conversation_id,
                chat,
                asset_fetcher=asset_fetcher,
                read_adapter="sync",
                direct_client_rejected=False,
            )

    def _pull_browser_and_persist(
        self,
        conversation_id: str,
        *,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        direct_client_rejected: bool = False,
    ) -> dict[str, Any]:
        result = fetch_conversation_same_origin(
            conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            timeout=browser_timeout,
        )
        return self._persist_chat(
            conversation_id,
            result.chat,
            read_adapter="browser",
            read_diagnostic=result.diagnostic,
            direct_client_rejected=direct_client_rejected,
        )

    def _pull_and_persist(
        self,
        conversation_id: str,
        *,
        browser_fallback: bool = True,
        direct_allowed: bool = True,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
    ) -> dict[str, Any]:
        if not direct_allowed:
            try:
                return self._pull_browser_and_persist(
                    conversation_id,
                    profile_dir=profile_dir,
                    browser_mode=browser_mode,
                    cdp_endpoint=cdp_endpoint,
                    browser_timeout=browser_timeout,
                    direct_client_rejected=True,
                )
            except Exception as browser_exc:  # noqa: BLE001 - preserve the sticky rejection boundary.
                raise DirectClientRejected(
                    f"direct exact-thread client was already rejected; browser fallback failed ({browser_exc})"
                ) from browser_exc

        try:
            return self._pull_sync_and_persist(conversation_id)
        except Exception as sync_exc:
            if not _is_direct_client_rejection(sync_exc) or not browser_fallback:
                raise
            try:
                return self._pull_browser_and_persist(
                    conversation_id,
                    profile_dir=profile_dir,
                    browser_mode=browser_mode,
                    cdp_endpoint=cdp_endpoint,
                    browser_timeout=browser_timeout,
                    direct_client_rejected=True,
                )
            except Exception as browser_exc:  # noqa: BLE001 - preserve both transport failures.
                raise DirectClientRejected(
                    f"direct exact-thread client rejected ({sync_exc}); browser fallback failed ({browser_exc})"
                ) from browser_exc

    def reconcile(
        self,
        operation_id: str,
        *,
        prompt: str,
        not_before: float,
        expected_attachments: Sequence[LocalAttachment] | None = None,
        browser_fallback: bool = True,
        direct_allowed: bool = True,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
    ) -> dict[str, Any]:
        with MutationOperationStore(self.db_path) as operations:
            op = operations.get(operation_id)
            if not op:
                raise KeyError(f"unknown mutation operation {operation_id}")
            conversation_id = str(op.get("conversation_id") or "").strip()
            if not conversation_id:
                raise RuntimeError("operation has no conversation id to reconcile")
            expected = list(expected_attachments) if expected_attachments is not None else _stored_expected_attachments(op)

            pulled = self._pull_and_persist(
                conversation_id,
                browser_fallback=browser_fallback,
                direct_allowed=direct_allowed,
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                browser_timeout=browser_timeout,
            )
            read_bits = {
                "read_adapter": pulled.get("read_adapter"),
                "read_diagnostic": pulled.get("read_diagnostic", {}),
                "direct_client_rejected": bool(pulled.get("direct_client_rejected")),
            }
            receipt = classify_prompt_mutation(pulled["chat"], prompt, not_before=not_before)
            if not receipt.prompt_committed:
                return operations.update(operation_id, state="commit_unknown") | read_bits

            user_node_id = receipt.user_node_ids[-1]
            committed_at = op.get("committed_at") or time.time()
            operations.update(operation_id, state="committed", committed_at=committed_at, user_node_id=user_node_id)

            attachment_ids: list[str] = []
            if expected:
                attachment_ok, canonical = match_expected_attachments(pulled["chat"], user_node_id, expected)
                if not attachment_ok:
                    return operations.update(
                        operation_id,
                        state="committed_pending",
                        committed_at=committed_at,
                        user_node_id=user_node_id,
                    ) | {"attachment_ids": []} | read_bits
                operations.record_canonical_attachments(operation_id, canonical)
                attachment_ids = [entry.file_id for entry in canonical if entry.file_id]
                with AttachmentArchive(self.db_path) as archive:
                    for local, remote in zip(expected, canonical):
                        archive.bind_local_copy(
                            conversation_id,
                            user_node_id,
                            remote.file_id,
                            remote.filename,
                            str(local.path),
                            local.sha256,
                        )

            assistant_node_id = _assistant_child_id(pulled["chat"], user_node_id)
            now = time.time()
            state = "complete" if assistant_node_id and receipt.assistant_text else "committed_pending"
            stored = operations.update(
                operation_id,
                state=state,
                committed_at=committed_at,
                completed_at=now if state == "complete" else None,
                user_node_id=user_node_id,
                assistant_node_id=assistant_node_id,
            )
            return stored | {
                "assistant_text": receipt.assistant_text,
                "attachment_ids": attachment_ids,
                "new_messages": pulled["new_messages"],
                "total_messages": pulled["total_messages"],
            } | read_bits

    def continue_thread(
        self,
        conversation: str,
        prompt: str,
        *,
        attachments: Sequence[Path | str] = (),
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        reconcile_attempts: int = 3,
        reconcile_interval: float = 1.0,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        local_attachments = [LocalAttachment.from_path(path) for path in attachments]
        started_at = time.time()

        with MutationOperationStore(self.db_path) as operations:
            op = operations.create(operation="continue", conversation_id=conversation, prompt=prompt)
            operation_id = str(op["operation_id"])
            operations.record_local_attachments(operation_id, local_attachments)
            operations.update(operation_id, state="browser_starting")
            try:
                browser = submit_prompt(
                    conversation,
                    prompt,
                    attachments=[item.path for item in local_attachments],
                    profile_dir=profile_dir,
                    browser_mode=browser_mode,
                    cdp_endpoint=cdp_endpoint,
                    timeout=browser_timeout,
                    follow=False,
                )
            except (BrowserWriteError, OSError, ValueError) as exc:
                state = "attachment_failed_precommit" if local_attachments and "attachment" in str(exc).lower() else "failed_precommit"
                operations.mark_error(operation_id, state, exc)
                raise

            conversation_id = str(browser.get("resolved_thread_id") or browser.get("conversation_id") or conversation).strip()
            stage_times = browser.get("stage_times") if isinstance(browser.get("stage_times"), dict) else {}
            now = time.time()
            operations.update(
                operation_id,
                state="submission_observed",
                conversation_id=conversation_id,
                authenticated_at=stage_times.get("authenticated") or (now if browser.get("authenticated") else None),
                hydrated_at=stage_times.get("hydrated") or (now if browser.get("hydrated") else None),
                attachment_ready_at=stage_times.get("attachment_ready"),
                submitted_at=stage_times.get("submission_observed") or now,
                transport="dom",
            )

        last: dict[str, Any] | None = None
        direct_context_rejected = False
        terminal_read_rejection = False
        for attempt in range(max(1, reconcile_attempts)):
            try:
                last = self.reconcile(
                    operation_id,
                    prompt=prompt,
                    not_before=started_at - 5.0,
                    expected_attachments=local_attachments,
                    direct_allowed=not direct_context_rejected,
                    profile_dir=profile_dir,
                    browser_mode=browser_mode,
                    cdp_endpoint=cdp_endpoint,
                    browser_timeout=browser_timeout,
                )
                direct_context_rejected = (
                    direct_context_rejected or bool(last.get("direct_client_rejected"))
                )
            except Exception as exc:
                rejected = _is_direct_client_rejection(exc)
                direct_context_rejected = direct_context_rejected or rejected
                terminal_read_rejection = rejected
                with MutationOperationStore(self.db_path) as operations:
                    last = operations.update(
                        operation_id,
                        state="commit_unknown",
                        error_class="DirectClientRejected" if rejected else exc.__class__.__name__,
                        error_detail=str(exc),
                    )
                break
            if last.get("state") in {"complete", "committed_pending"}:
                break
            if attempt + 1 < max(1, reconcile_attempts):
                time.sleep(max(0.0, reconcile_interval))

        if last is None:
            last = self.operation_status(operation_id) or {"operation_id": operation_id, "state": "commit_unknown"}

        if (
            follow
            and not terminal_read_rejection
            and last.get("error_class") != "DirectClientRejected"
            and last.get("state") in {"committed", "committed_pending", "commit_unknown"}
        ):
            deadline = time.monotonic() + max(0.0, follow_timeout)
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))
                try:
                    last = self.reconcile(
                        operation_id,
                        prompt=prompt,
                        not_before=started_at - 5.0,
                        expected_attachments=local_attachments,
                        direct_allowed=not direct_context_rejected,
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        browser_timeout=browser_timeout,
                    )
                    direct_context_rejected = (
                        direct_context_rejected or bool(last.get("direct_client_rejected"))
                    )
                except Exception as exc:
                    rejected = _is_direct_client_rejection(exc)
                    direct_context_rejected = direct_context_rejected or rejected
                    with MutationOperationStore(self.db_path) as operations:
                        last = operations.update(
                            operation_id,
                            state="commit_unknown",
                            error_class="DirectClientRejected" if rejected else exc.__class__.__name__,
                            error_detail=str(exc),
                        )
                    if rejected:
                        break
                    continue
                if last.get("state") == "complete":
                    break
        return last
