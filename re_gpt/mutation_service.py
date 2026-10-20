"""Durable mutation controller with adapter-independent graph reconciliation.

Direct async HTTP is the preferred product transport. Browser/DOM remains a
pre-commit fallback for ordinary existing-thread continuation, plus bootstrap,
protocol-oracle and diagnostic work. Every accepted mutation is certified from
an exact conversation graph and persisted through the same SQLite path.
"""

from __future__ import annotations

import sys
import time
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .async_mutation_probe import (
    AmbiguousAsyncWriteError,
    ExplicitAsyncWriteRejection,
    submit_async_prompt_once,
)
from .attachment_archive import AttachmentArchive
from .attachments import LocalAttachment, match_expected_attachments
from .browser_write import BrowserWriteError, submit_prompt
from .direct_graph_mutation import submit_direct_graph_mutation_once
from .graph_broker import GraphAcquisitionFailed, fetch_exact_graph
from .mutation_receipt import classify_prompt_mutation, find_turn_assistant_nodes
from .operation_store import MutationOperationStore
from .read_plane import install_validated_sync_read_plane
from .storage import ConversationStorage, DEFAULT_DB_PATH, extract_ordered_messages

install_validated_sync_read_plane()


class DirectClientRejected(RuntimeError):
    """Backward-compatible alias for older callers; broker failures supersede it."""


def _assistant_child_id(chat: dict[str, Any], user_node_id: str) -> str | None:
    mapping = chat.get("mapping")
    if not isinstance(mapping, dict):
        return None
    candidates = find_turn_assistant_nodes(mapping, user_node_id)
    if not candidates:
        return None
    return candidates[-1][2]


def _message_role(node: Mapping[str, Any] | None) -> str:
    if not isinstance(node, Mapping):
        return ""
    message = node.get("message")
    if not isinstance(message, Mapping):
        return ""
    author = message.get("author")
    return str(author.get("role") or "").strip() if isinstance(author, Mapping) else ""


def _message_text(node: Mapping[str, Any] | None) -> str:
    if not isinstance(node, Mapping):
        return ""
    message = node.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if not isinstance(content, Mapping):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "\n".join(str(item) for item in parts if isinstance(item, str)).strip()


def _node_parent(chat: Mapping[str, Any], node_id: str) -> str | None:
    mapping = chat.get("mapping")
    if not isinstance(mapping, Mapping):
        return None
    node = mapping.get(node_id)
    if not isinstance(node, Mapping):
        return None
    raw = node.get("parent")
    return str(raw).strip() if raw is not None and str(raw).strip() else None


def _node_exists_with_role(chat: Mapping[str, Any], node_id: str, role: str | None = None) -> bool:
    mapping = chat.get("mapping")
    if not isinstance(mapping, Mapping):
        return False
    node = mapping.get(node_id)
    if not isinstance(node, Mapping):
        return False
    return role is None or _message_role(node) == role


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
        read_adapter: str,
        read_diagnostic: dict[str, Any] | None = None,
        attempted_read_adapters: Sequence[str] = (),
        rejected_read_adapters: Sequence[str] = (),
        read_observations: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        messages = extract_ordered_messages(chat)
        with ConversationStorage(db_path=self.db_path, write_json=self.export_json) as storage:
            persisted = storage.persist_chat(
                conversation_id,
                chat,
                messages,
                asset_fetcher=None,
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
            "attempted_read_adapters": list(attempted_read_adapters),
            "rejected_read_adapters": list(rejected_read_adapters),
            "read_observations": [dict(item) for item in read_observations],
        }

    def _pull_and_persist(
        self,
        conversation_id: str,
        *,
        browser_fallback: bool = True,
        direct_allowed: bool = True,
        skip_read_adapters: Sequence[str] = (),
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
    ) -> dict[str, Any]:
        skip = {str(item) for item in skip_read_adapters}
        order = [name for name in ("async", "sync", "browser") if name not in skip]
        if not browser_fallback:
            order = [name for name in order if name != "browser"]
        if not direct_allowed:
            order = [name for name in order if name == "browser"]
        result = fetch_exact_graph(
            conversation_id,
            session_token=self.session_token,
            model=self.model,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            adapter_order=tuple(order),
        )
        return self._persist_chat(
            conversation_id,
            result.graph,
            read_adapter=result.adapter,
            read_diagnostic=result.diagnostic,
            attempted_read_adapters=result.attempted_adapters,
            rejected_read_adapters=result.rejected_adapters,
            read_observations=result.observations,
        )

    @staticmethod
    def _read_bits(pulled: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "read_adapter": pulled.get("read_adapter"),
            "read_diagnostic": pulled.get("read_diagnostic", {}),
            "attempted_read_adapters": pulled.get("attempted_read_adapters", []),
            "rejected_read_adapters": pulled.get("rejected_read_adapters", []),
            "read_observations": pulled.get("read_observations", []),
        }

    def _bind_attachments(
        self,
        operations: MutationOperationStore,
        operation_id: str,
        conversation_id: str,
        chat: dict[str, Any],
        user_node_id: str,
        expected: Sequence[LocalAttachment],
    ) -> tuple[bool, list[str]]:
        if not expected:
            return True, []
        attachment_ok, canonical = match_expected_attachments(chat, user_node_id, expected)
        if not attachment_ok:
            return False, []
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
        return True, attachment_ids

    def reconcile(
        self,
        operation_id: str,
        *,
        prompt: str,
        not_before: float,
        expected_attachments: Sequence[LocalAttachment] | None = None,
        browser_fallback: bool = True,
        direct_allowed: bool = True,
        skip_read_adapters: Sequence[str] = (),
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
                skip_read_adapters=skip_read_adapters,
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                browser_timeout=browser_timeout,
            )
            read_bits = self._read_bits(pulled)
            receipt = classify_prompt_mutation(pulled["chat"], prompt, not_before=not_before)
            if not receipt.prompt_committed:
                return operations.update(operation_id, state="commit_unknown", error_class=None, error_detail=None) | read_bits

            user_node_id = receipt.user_node_ids[-1]
            committed_at = op.get("committed_at") or time.time()
            operations.update(
                operation_id,
                state="committed",
                committed_at=committed_at,
                user_node_id=user_node_id,
                error_class=None,
                error_detail=None,
            )
            attachment_ok, attachment_ids = self._bind_attachments(
                operations, operation_id, conversation_id, pulled["chat"], user_node_id, expected
            )
            if not attachment_ok:
                return operations.update(
                    operation_id,
                    state="committed_pending",
                    committed_at=committed_at,
                    user_node_id=user_node_id,
                    error_class=None,
                    error_detail=None,
                ) | {"attachment_ids": []} | read_bits

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
                error_class=None,
                error_detail=None,
            )
            return stored | {
                "assistant_text": receipt.assistant_text,
                "attachment_ids": attachment_ids,
                "new_messages": pulled["new_messages"],
                "total_messages": pulled["total_messages"],
            } | read_bits

    def _reconcile_graph_mutation(
        self,
        operation_id: str,
        *,
        prompt: str | None,
        expected_user_message_id: str | None = None,
        expected_assistant_message_id: str | None = None,
        expected_attachments: Sequence[LocalAttachment] = (),
        skip_read_adapters: Sequence[str] = (),
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
                raise RuntimeError("graph mutation has no assigned conversation id")
            pulled = self._pull_and_persist(
                conversation_id,
                skip_read_adapters=skip_read_adapters,
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                browser_timeout=browser_timeout,
            )
            chat = pulled["chat"]
            mapping = chat.get("mapping") if isinstance(chat, dict) else None
            read_bits = self._read_bits(pulled)
            if not isinstance(mapping, dict):
                return operations.update(operation_id, state="commit_unknown") | read_bits

            operation = str(op.get("operation") or "")
            target_parent = str(op.get("requested_parent_node_id") or "").strip() or None
            started_at = float(op.get("started_at") or 0.0)

            if operation == "regenerate":
                user_node_id = target_parent
                if not user_node_id or not _node_exists_with_role(chat, user_node_id, "user"):
                    return operations.update(operation_id, state="commit_unknown") | read_bits
                candidates = find_turn_assistant_nodes(mapping, user_node_id)
                selected: tuple[int, float, str, str] | None = None
                if expected_assistant_message_id:
                    selected = next((item for item in candidates if item[2] == expected_assistant_message_id), None)
                if selected is None:
                    recent = [item for item in candidates if item[1] >= started_at - 5.0]
                    selected = recent[-1] if recent else None
                if selected is None:
                    return operations.update(operation_id, state="commit_unknown") | read_bits
                has_text, _created, assistant_node_id, assistant_text = selected
                committed_at = op.get("committed_at") or time.time()
                state = "complete" if has_text and assistant_text else "committed_pending"
                stored = operations.update(
                    operation_id,
                    state=state,
                    committed_at=committed_at,
                    completed_at=time.time() if state == "complete" else None,
                    user_node_id=user_node_id,
                    assistant_node_id=assistant_node_id,
                    error_class=None,
                    error_detail=None,
                )
                return stored | {
                    "assistant_text": assistant_text or None,
                    "attachment_ids": [],
                    "new_messages": pulled["new_messages"],
                    "total_messages": pulled["total_messages"],
                } | read_bits

            expected_prompt = str(prompt or "").strip()
            candidate_user_ids: list[str] = []
            if expected_user_message_id:
                node = mapping.get(expected_user_message_id)
                if isinstance(node, Mapping) and _message_role(node) == "user" and _message_text(node) == expected_prompt:
                    if target_parent is None or _node_parent(chat, expected_user_message_id) == target_parent:
                        candidate_user_ids.append(expected_user_message_id)
            if not candidate_user_ids:
                receipt = classify_prompt_mutation(chat, expected_prompt, not_before=started_at - 5.0)
                candidate_user_ids = [
                    node_id
                    for node_id in receipt.user_node_ids
                    if target_parent is None or _node_parent(chat, node_id) == target_parent
                ]
            if not candidate_user_ids:
                return operations.update(operation_id, state="commit_unknown") | read_bits

            user_node_id = candidate_user_ids[-1]
            committed_at = op.get("committed_at") or time.time()
            operations.update(
                operation_id,
                state="committed",
                committed_at=committed_at,
                user_node_id=user_node_id,
                error_class=None,
                error_detail=None,
            )
            attachment_ok, attachment_ids = self._bind_attachments(
                operations, operation_id, conversation_id, chat, user_node_id, expected_attachments
            )
            if not attachment_ok:
                return operations.update(
                    operation_id,
                    state="committed_pending",
                    user_node_id=user_node_id,
                    committed_at=committed_at,
                ) | {"attachment_ids": []} | read_bits

            assistants = find_turn_assistant_nodes(mapping, user_node_id)
            assistant_node_id: str | None = None
            assistant_text: str | None = None
            if expected_assistant_message_id:
                exact = next((item for item in assistants if item[2] == expected_assistant_message_id), None)
                if exact:
                    assistant_node_id, assistant_text = exact[2], exact[3] or None
            if assistant_node_id is None and assistants:
                assistant_node_id = assistants[-1][2]
                assistant_text = assistants[-1][3] or None
            state = "complete" if assistant_node_id and assistant_text else "committed_pending"
            stored = operations.update(
                operation_id,
                state=state,
                committed_at=committed_at,
                completed_at=time.time() if state == "complete" else None,
                user_node_id=user_node_id,
                assistant_node_id=assistant_node_id,
                error_class=None,
                error_detail=None,
            )
            # Confirmed exact graph only. No network call, mutation resend, or
            # secondary transcript persistence is introduced by this projection.
            try:
                from .canonical_archive_hydrator import hydrate_verified_graph
                account = os.environ.get("ITIR_CHATGPT_ACCOUNT", "").strip()
                if not account:
                    archive_delta = {"state": "deferred", "reason": "explicit_account_required"}
                else:
                    archive_delta = {"state": "ingested", **hydrate_verified_graph(
                        chat, conversation_id=conversation_id, account=account)}
            except Exception as exc:
                # Mutation commit remains true; archive projection failure is
                # separate, never a reason to retry or resubmit the operation.
                archive_delta = {"state": "deferred",
                                 "reason": type(exc).__name__}
            return stored | {
                "canonical_archive_delta": archive_delta,
                "assistant_text": assistant_text,
                "attachment_ids": attachment_ids,
                "new_messages": pulled["new_messages"],
                "total_messages": pulled["total_messages"],
            } | read_bits

    def _run_graph_mutation(
        self,
        operation: str,
        *,
        conversation_id: str | None,
        parent_node_id: str | None,
        prompt: str | None,
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
        local_attachments = [LocalAttachment.from_path(path) for path in attachments]
        with MutationOperationStore(self.db_path) as operations:
            op = operations.create(
                operation=operation,
                conversation_id=conversation_id,
                prompt=prompt or "",
                requested_parent_node_id=parent_node_id,
            )
            operation_id = str(op["operation_id"])
            operations.record_local_attachments(operation_id, local_attachments)
            operations.update(operation_id, state="async_starting", transport="async")

        try:
            submission = submit_direct_graph_mutation_once(
                operation,
                conversation_id=conversation_id,
                parent_node_id=parent_node_id,
                prompt=prompt,
                attachments=local_attachments,
                session_token=self.session_token,
                model=self.model,
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                browser_timeout=browser_timeout,
            )
        except ExplicitAsyncWriteRejection as exc:
            state = (
                "attachment_failed_precommit"
                if local_attachments and str(getattr(exc, "stage", "")).startswith("attachment")
                else "failed_precommit"
            )
            with MutationOperationStore(self.db_path) as operations:
                operations.mark_error(operation_id, state, exc)
            raise
        except AmbiguousAsyncWriteError as exc:
            observations = [item.to_dict() for item in exc.observations]
            with MutationOperationStore(self.db_path) as operations:
                return operations.update(
                    operation_id,
                    state="commit_unknown",
                    transport="async",
                    error_class=exc.__class__.__name__,
                    error_detail=str(exc),
                ) | {"write_observations": observations}

        resolved_id = str(submission.get("resolved_thread_id") or submission.get("conversation_id") or conversation_id or "").strip()
        if not resolved_id:
            with MutationOperationStore(self.db_path) as operations:
                return operations.update(
                    operation_id,
                    state="commit_unknown",
                    transport="async",
                    error_class="MissingConversationId",
                    error_detail="mutation response did not identify a conversation",
                ) | {"write_observations": list(submission.get("protocol_observations") or [])}

        stage_times = submission.get("stage_times") if isinstance(submission.get("stage_times"), dict) else {}
        now = time.time()
        with MutationOperationStore(self.db_path) as operations:
            operations.update(
                operation_id,
                state="submission_observed",
                conversation_id=resolved_id,
                authenticated_at=stage_times.get("authenticated") or now,
                hydrated_at=stage_times.get("hydrated"),
                attachment_ready_at=stage_times.get("attachment_ready"),
                submitted_at=stage_times.get("submission_observed") or now,
                transport="async",
            )

        expected_user_id = str(submission.get("user_message_id") or "").strip() or None
        expected_assistant_id = str(submission.get("assistant_message_id") or "").strip() or None
        write_observations = list(submission.get("protocol_observations") or [])
        rejected_read_adapters: set[str] = set()
        last: dict[str, Any] | None = None

        def reconcile_once() -> dict[str, Any]:
            return self._reconcile_graph_mutation(
                operation_id,
                prompt=prompt,
                expected_user_message_id=expected_user_id,
                expected_assistant_message_id=expected_assistant_id,
                expected_attachments=local_attachments,
                skip_read_adapters=tuple(sorted(rejected_read_adapters)),
                profile_dir=profile_dir,
                browser_mode=browser_mode,
                cdp_endpoint=cdp_endpoint,
                browser_timeout=browser_timeout,
            )

        for attempt in range(max(1, reconcile_attempts)):
            try:
                last = reconcile_once()
                rejected_read_adapters.update(last.get("rejected_read_adapters") or ())
            except GraphAcquisitionFailed as exc:
                rejected_read_adapters.update(exc.rejected_adapters)
                with MutationOperationStore(self.db_path) as operations:
                    last = operations.update(
                        operation_id,
                        state="commit_unknown",
                        error_class="GraphAcquisitionFailed",
                        error_detail=str(exc),
                    ) | {
                        "attempted_read_adapters": list(exc.attempted_adapters),
                        "rejected_read_adapters": list(exc.rejected_adapters),
                        "read_observations": list(exc.observations),
                    }
                break
            except Exception as exc:  # noqa: BLE001 - post-submit state must remain durable.
                with MutationOperationStore(self.db_path) as operations:
                    last = operations.update(
                        operation_id,
                        state="commit_unknown",
                        error_class=exc.__class__.__name__,
                        error_detail=str(exc),
                    )
                break
            if last.get("state") in {"complete", "committed_pending"}:
                break
            if attempt + 1 < max(1, reconcile_attempts):
                time.sleep(max(0.0, reconcile_interval))

        if last is None:
            last = self.operation_status(operation_id) or {"operation_id": operation_id, "state": "commit_unknown"}

        if follow and last.get("state") in {"committed", "committed_pending", "commit_unknown"}:
            deadline = time.monotonic() + max(0.0, follow_timeout)
            while time.monotonic() < deadline:
                time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
                try:
                    last = reconcile_once()
                    rejected_read_adapters.update(last.get("rejected_read_adapters") or ())
                except GraphAcquisitionFailed:
                    break
                except Exception:
                    continue
                if last.get("state") == "complete":
                    break

        if write_observations and "write_observations" not in last:
            last = dict(last) | {"write_observations": write_observations}
        return last

    def new_conversation(
        self,
        prompt: str,
        *,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        reconcile_attempts: int = 3,
        reconcile_interval: float = 1.0,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")
        return self._run_graph_mutation(
            "new_conversation",
            conversation_id=None,
            parent_node_id=None,
            prompt=prompt,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            reconcile_attempts=reconcile_attempts,
            reconcile_interval=reconcile_interval,
            follow=follow,
            follow_timeout=follow_timeout,
        )

    def branch_from_node(
        self,
        conversation: str,
        parent_node_id: str,
        prompt: str,
        *,
        attachments: Sequence[Path | str] = (),
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        conversation = str(conversation or "").strip()
        parent_node_id = str(parent_node_id or "").strip()
        prompt = str(prompt or "").strip()
        if not conversation or not parent_node_id or not prompt:
            raise ValueError("branch requires conversation, parent_node_id, and prompt")
        before = self._pull_and_persist(
            conversation,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )["chat"]
        if not _node_exists_with_role(before, parent_node_id):
            raise ValueError(f"parent node not found: {parent_node_id}")
        return self._run_graph_mutation(
            "branch",
            conversation_id=conversation,
            parent_node_id=parent_node_id,
            prompt=prompt,
            attachments=attachments,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            follow=follow,
            follow_timeout=follow_timeout,
        )

    def regenerate(
        self,
        conversation: str,
        user_node_id: str,
        *,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        conversation = str(conversation or "").strip()
        user_node_id = str(user_node_id or "").strip()
        if not conversation or not user_node_id:
            raise ValueError("regenerate requires conversation and user_node_id")
        before = self._pull_and_persist(
            conversation,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )["chat"]
        if not _node_exists_with_role(before, user_node_id, "user"):
            raise ValueError(f"user node not found: {user_node_id}")
        return self._run_graph_mutation(
            "regenerate",
            conversation_id=conversation,
            parent_node_id=user_node_id,
            prompt=None,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            follow=follow,
            follow_timeout=follow_timeout,
        )

    def edit_and_branch(
        self,
        conversation: str,
        user_node_id: str,
        prompt: str,
        *,
        attachments: Sequence[Path | str] = (),
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        cdp_endpoint: str | None = None,
        browser_timeout: int = 60,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        conversation = str(conversation or "").strip()
        user_node_id = str(user_node_id or "").strip()
        prompt = str(prompt or "").strip()
        if not conversation or not user_node_id or not prompt:
            raise ValueError("edit-and-branch requires conversation, user_node_id, and prompt")
        before = self._pull_and_persist(
            conversation,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )["chat"]
        if not _node_exists_with_role(before, user_node_id, "user"):
            raise ValueError(f"user node not found: {user_node_id}")
        parent_node_id = _node_parent(before, user_node_id)
        if not parent_node_id or parent_node_id == "client-created-root":
            raise ValueError(
                f"user node {user_node_id} has no branchable parent in this conversation "
                f"(root turn cannot be branched within the same conversation; use new_conversation instead)"
            )
        return self._run_graph_mutation(
            "edit_branch",
            conversation_id=conversation,
            parent_node_id=parent_node_id,
            prompt=prompt,
            attachments=attachments,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            follow=follow,
            follow_timeout=follow_timeout,
        )

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
        write_transport: str = "dom",
        dom_fallback: bool = False,
        reconcile_attempts: int = 3,
        reconcile_interval: float = 1.0,
        follow: bool = False,
        follow_timeout: float = 60.0,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        if write_transport not in {"dom", "async", "auto"}:
            raise ValueError(f"unsupported write_transport {write_transport!r}; expected 'dom', 'async', or 'auto'")
        local_attachments = [LocalAttachment.from_path(path) for path in attachments]
        started_at = time.time()

        try_async = write_transport in {"async", "auto"}
        allow_dom_fallback = write_transport == "auto" or (write_transport == "async" and bool(dom_fallback))
        effective_transport = "async" if try_async else "dom"
        pre_fallback_observations: list[dict[str, Any]] = []
        write_observations: list[dict[str, Any]] = []

        with MutationOperationStore(self.db_path) as operations:
            op = operations.create(operation="continue", conversation_id=conversation, prompt=prompt)
            operation_id = str(op["operation_id"])
            operations.record_local_attachments(operation_id, local_attachments)

            submission: dict[str, Any] | None = None
            if try_async:
                operations.update(operation_id, state="async_starting", transport="async")
                try:
                    submission = submit_async_prompt_once(
                        conversation,
                        prompt,
                        attachments=local_attachments,
                        session_token=self.session_token,
                        model=self.model,
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        browser_timeout=browser_timeout,
                        progress_callback=progress_callback,
                    )
                except ExplicitAsyncWriteRejection as exc:
                    if not allow_dom_fallback:
                        state = (
                            "attachment_failed_precommit"
                            if local_attachments and str(getattr(exc, "stage", "")).startswith("attachment")
                            else "failed_precommit"
                        )
                        operations.mark_error(operation_id, state, exc)
                        raise
                    pre_fallback_observations = [obs.to_dict() for obs in exc.observations]
                    effective_transport = "dom"
                except AmbiguousAsyncWriteError as exc:
                    write_observations = [obs.to_dict() for obs in exc.observations]
                    return operations.update(
                        operation_id,
                        state="commit_unknown",
                        transport="async",
                        error_class=exc.__class__.__name__,
                        error_detail=str(exc),
                    ) | {"write_observations": write_observations}

            if submission is None:
                effective_transport = "dom"
                operations.update(operation_id, state="browser_starting", transport="dom")
                try:
                    submission = submit_prompt(
                        conversation,
                        prompt,
                        attachments=[item.path for item in local_attachments],
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        timeout=browser_timeout,
                        follow=follow,
                    )
                except (BrowserWriteError, OSError, ValueError) as exc:
                    state = "attachment_failed_precommit" if local_attachments and "attachment" in str(exc).lower() else "failed_precommit"
                    operations.mark_error(operation_id, state, exc)
                    raise

            write_observations = pre_fallback_observations + list(submission.get("protocol_observations") or [])
            conversation_id = str(submission.get("resolved_thread_id") or submission.get("conversation_id") or conversation).strip()
            stage_times = submission.get("stage_times") if isinstance(submission.get("stage_times"), dict) else {}
            now = time.time()
            operations.update(
                operation_id,
                state="submission_observed",
                conversation_id=conversation_id,
                authenticated_at=stage_times.get("authenticated") or (now if submission.get("authenticated") else None),
                hydrated_at=stage_times.get("hydrated") or (now if submission.get("hydrated") else None),
                attachment_ready_at=stage_times.get("attachment_ready"),
                submitted_at=stage_times.get("submission_observed") or now,
                transport=effective_transport,
            )

        last: dict[str, Any] | None = None
        rejected_read_adapters: set[str] = set()
        if reconcile_attempts > 0:
            for attempt in range(reconcile_attempts):
                try:
                    last = self.reconcile(
                        operation_id,
                        prompt=prompt,
                        not_before=started_at - 5.0,
                        expected_attachments=local_attachments,
                        skip_read_adapters=tuple(sorted(rejected_read_adapters)),
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        browser_timeout=browser_timeout,
                    )
                    rejected_read_adapters.update(last.get("rejected_read_adapters") or ())
                except GraphAcquisitionFailed as exc:
                    rejected_read_adapters.update(exc.rejected_adapters)
                    with MutationOperationStore(self.db_path) as operations:
                        last = operations.update(
                            operation_id,
                            state="commit_unknown",
                            error_class="GraphAcquisitionFailed",
                            error_detail=str(exc),
                        ) | {
                            "attempted_read_adapters": list(exc.attempted_adapters),
                            "rejected_read_adapters": list(exc.rejected_adapters),
                            "read_observations": list(exc.observations),
                        }
                    break
                except Exception as exc:  # noqa: BLE001 - preserve durable post-submit state.
                    with MutationOperationStore(self.db_path) as operations:
                        last = operations.update(
                            operation_id,
                            state="commit_unknown",
                            error_class=exc.__class__.__name__,
                            error_detail=str(exc),
                        )
                    break
                if last.get("state") in {"complete", "committed_pending"}:
                    break
                if attempt + 1 < reconcile_attempts:
                    time.sleep(max(0.0, reconcile_interval))

        if last is None:
            last = self.operation_status(operation_id) or {"operation_id": operation_id, "state": "commit_unknown"}

        all_adapters_rejected = {"async", "sync", "browser"}.issubset(rejected_read_adapters)
        if (
            follow
            and not all_adapters_rejected
            and last.get("error_class") != "GraphAcquisitionFailed"
            and last.get("state") in {"committed", "committed_pending", "commit_unknown"}
        ):
            deadline = time.monotonic() + max(0.0, follow_timeout)
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                time.sleep(min(2.0, remaining))
                try:
                    last = self.reconcile(
                        operation_id,
                        prompt=prompt,
                        not_before=started_at - 5.0,
                        expected_attachments=local_attachments,
                        skip_read_adapters=tuple(sorted(rejected_read_adapters)),
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        browser_timeout=browser_timeout,
                    )
                    rejected_read_adapters.update(last.get("rejected_read_adapters") or ())
                except GraphAcquisitionFailed as exc:
                    rejected_read_adapters.update(exc.rejected_adapters)
                    with MutationOperationStore(self.db_path) as operations:
                        prior = operations.get(operation_id)
                        if prior and prior.get("state") in {"committed", "committed_pending", "complete"}:
                            last = (dict(last) if isinstance(last, dict) else {}) | prior
                        else:
                            last = operations.update(
                                operation_id,
                                state="commit_unknown",
                                error_class="GraphAcquisitionFailed",
                                error_detail=str(exc),
                            ) | {
                                "attempted_read_adapters": list(exc.attempted_adapters),
                                "rejected_read_adapters": list(exc.rejected_adapters),
                                "read_observations": list(exc.observations),
                            }
                    break
                except Exception as exc:  # noqa: BLE001
                    with MutationOperationStore(self.db_path) as operations:
                        prior = operations.get(operation_id)
                        if prior and prior.get("state") in {"committed", "committed_pending", "complete"}:
                            last = (dict(last) if isinstance(last, dict) else {}) | prior
                        else:
                            last = operations.update(
                                operation_id,
                                state="commit_unknown",
                                error_class=exc.__class__.__name__,
                                error_detail=str(exc),
                            )
                    continue
                if last.get("state") == "complete":
                    break
        if write_observations and "write_observations" not in last:
            last = dict(last) | {"write_observations": write_observations}
        if submission:
            for key in ("stream_terminal_observed", "stream_terminal_reason", "stream_event_counts", "assistant_stream_text"):
                if key in submission and key not in last:
                    last = dict(last) | {key: submission[key]}
        return last


def continue_thread(
    conversation: str,
    prompt: str,
    *,
    attachments: Sequence[Path | str] = (),
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    write_transport: str = "async",
    dom_fallback: bool = False,
    reconcile_attempts: int = 3,
    reconcile_interval: float = 1.0,
    follow: bool = False,
    follow_timeout: float = 60.0,
    progress_callback: Any = None,
    stream: bool = False,
    output: Path | str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    session_token: str | None = None,
    model: str | None = None,
    export_json: bool = False,
) -> dict[str, Any]:
    """Top-level convenience function to continue an existing thread."""
    if stream and progress_callback is None:
        def on_progress(event: dict[str, Any]) -> None:
            if event.get("kind") == "token_delta":
                delta = str(event.get("delta") or "")
                if delta:
                    sys.stdout.write(delta)
                    sys.stdout.flush()

        progress_callback = on_progress
        follow = True

    service = MutationService(
        session_token=session_token,
        model=model,
        export_json=export_json,
        db_path=db_path,
    )
    res = service.continue_thread(
        conversation,
        prompt,
        attachments=attachments,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        write_transport=write_transport,
        dom_fallback=dom_fallback,
        reconcile_attempts=reconcile_attempts,
        reconcile_interval=reconcile_interval,
        follow=follow,
        follow_timeout=follow_timeout,
        progress_callback=progress_callback,
    )
    if stream:
        sys.stdout.write("\n")
        sys.stdout.flush()
    if output:
        out_path = Path(output).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        reply = str(res.get("assistant_text") or "")
        out_path.write_text(reply + ("\n" if reply else ""), encoding="utf-8")
    return res


def new_conversation(
    prompt: str,
    *,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    reconcile_attempts: int = 3,
    reconcile_interval: float = 1.0,
    follow: bool = False,
    follow_timeout: float = 60.0,
    stream: bool = False,
    output: Path | str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    session_token: str | None = None,
    model: str | None = None,
    export_json: bool = False,
) -> dict[str, Any]:
    """Top-level convenience function to start a new conversation."""
    service = MutationService(
        session_token=session_token,
        model=model,
        export_json=export_json,
        db_path=db_path,
    )
    res = service.new_conversation(
        prompt,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        reconcile_attempts=reconcile_attempts,
        reconcile_interval=reconcile_interval,
        follow=follow or stream,
        follow_timeout=follow_timeout,
    )
    if output:
        out_path = Path(output).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        reply = str(res.get("assistant_text") or "")
        out_path.write_text(reply + ("\n" if reply else ""), encoding="utf-8")
    return res
