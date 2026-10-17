"""Durable mutation controller with adapter-independent graph reconciliation.

The browser DOM remains the default writer until an HTTP writer proves canonical
success. Exact graph reads are acquired through the async-first broker and all
accepted graphs share one persistence/receipt path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from .async_mutation_probe import (
    AmbiguousAsyncWriteError,
    ExplicitAsyncWriteRejection,
    submit_async_prompt_once,
)
from .attachment_archive import AttachmentArchive
from .attachments import LocalAttachment, match_expected_attachments
from .browser_write import BrowserWriteError, submit_prompt
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
        # `direct_allowed` is retained for compatibility with callers from the
        # pre-broker API. New code expresses quarantine with skip_read_adapters.
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
            read_bits = {
                "read_adapter": pulled.get("read_adapter"),
                "read_diagnostic": pulled.get("read_diagnostic", {}),
                "attempted_read_adapters": pulled.get("attempted_read_adapters", []),
                "rejected_read_adapters": pulled.get("rejected_read_adapters", []),
                "read_observations": pulled.get("read_observations", []),
            }
            receipt = classify_prompt_mutation(pulled["chat"], prompt, not_before=not_before)
            if not receipt.prompt_committed:
                return operations.update(
                    operation_id,
                    state="commit_unknown",
                    error_class=None,
                    error_detail=None,
                ) | read_bits

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

            attachment_ids: list[str] = []
            if expected:
                attachment_ok, canonical = match_expected_attachments(pulled["chat"], user_node_id, expected)
                if not attachment_ok:
                    return operations.update(
                        operation_id,
                        state="committed_pending",
                        committed_at=committed_at,
                        user_node_id=user_node_id,
                        error_class=None,
                        error_detail=None,
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
                error_class=None,
                error_detail=None,
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
        write_transport: str = "dom",
        reconcile_attempts: int = 3,
        reconcile_interval: float = 1.0,
        follow: bool = False,
        follow_timeout: float = 60.0,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        if write_transport not in {"dom", "async"}:
            raise ValueError(f"unsupported write_transport {write_transport!r}; expected 'dom' or 'async'")
        local_attachments = [LocalAttachment.from_path(path) for path in attachments]
        if write_transport == "async" and local_attachments:
            raise ValueError("async HTTP mutation probe does not synthesize attachment upload endpoints")
        started_at = time.time()

        write_observations: list[dict[str, Any]] = []
        with MutationOperationStore(self.db_path) as operations:
            op = operations.create(operation="continue", conversation_id=conversation, prompt=prompt)
            operation_id = str(op["operation_id"])
            operations.record_local_attachments(operation_id, local_attachments)

            if write_transport == "async":
                operations.update(operation_id, state="async_starting", transport="async")
                try:
                    submission = submit_async_prompt_once(
                        conversation,
                        prompt,
                        session_token=self.session_token,
                        model=self.model,
                        profile_dir=profile_dir,
                        browser_mode=browser_mode,
                        cdp_endpoint=cdp_endpoint,
                        browser_timeout=browser_timeout,
                    )
                except ExplicitAsyncWriteRejection as exc:
                    operations.mark_error(operation_id, "failed_precommit", exc)
                    raise
                except AmbiguousAsyncWriteError as exc:
                    write_observations = [obs.to_dict() for obs in exc.observations]
                    return operations.update(
                        operation_id,
                        state="commit_unknown",
                        transport="async",
                        error_class=exc.__class__.__name__,
                        error_detail=str(exc),
                    ) | {"write_observations": write_observations}
            else:
                operations.update(operation_id, state="browser_starting")
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

            write_observations = list(submission.get("protocol_observations") or [])
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
                transport=write_transport,
            )

        last: dict[str, Any] | None = None
        rejected_read_adapters: set[str] = set()
        for attempt in range(max(1, reconcile_attempts)):
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
            if attempt + 1 < max(1, reconcile_attempts):
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
        return last
