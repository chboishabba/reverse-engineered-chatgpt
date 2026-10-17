"""One-shot accessibility capture orchestration for browser-owned mutations.

This module deliberately separates the single mutation actuator from repeatable
read/reconciliation work.  Once the browser actuator has been invoked it is
never called again by the same capture, even if its outcome is ambiguous.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from chatgpt_dom_relay import DEFAULT_ALIAS_PATHS, extract_thread_id, load_alias_map

from .attachments import LocalAttachment
from .browser_write import submit_prompt
from .experiment_receipt import build_experiment_receipt
from .graph_broker import fetch_exact_graph
from .mutation_service import MutationService
from .operation_store import MutationOperationStore
from .storage import DEFAULT_DB_PATH


def _resolve_conversation_id(selector: str) -> str:
    raw = str(selector or "").strip()
    if not raw:
        raise ValueError("conversation selector is empty")
    thread_id = extract_thread_id(raw)
    if thread_id:
        return thread_id
    aliases = load_alias_map(list(DEFAULT_ALIAS_PATHS))
    alias = aliases.get(raw.lower())
    if alias:
        return alias
    # Dependency-injected tests and future exact-read adapters may use opaque
    # conversation keys.  The production browser actuator will still reject an
    # unresolved selector before it can commit anything.
    return raw


def _default_graph_reader(
    conversation_id: str,
    *,
    profile_dir: Path | str | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
):
    return fetch_exact_graph(
        conversation_id,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
    )


def _default_actuator(
    selector: str,
    prompt: str,
    attachments: Sequence[LocalAttachment],
    *,
    profile_dir: Path | str | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
    follow: bool,
) -> dict[str, Any]:
    return submit_prompt(
        selector,
        prompt,
        attachments=[item.path for item in attachments],
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=browser_timeout,
        follow=follow,
    )


def _default_reconciler(
    *,
    operation_id: str,
    prompt: str,
    not_before: float,
    expected_attachments: Sequence[LocalAttachment],
    db_path: Path | str,
    profile_dir: Path | str | None,
    browser_mode: str,
    cdp_endpoint: str | None,
    browser_timeout: int,
) -> dict[str, Any]:
    service = MutationService(db_path=db_path)
    return service.reconcile(
        operation_id,
        prompt=prompt,
        not_before=not_before,
        expected_attachments=expected_attachments,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
    )


@dataclass(frozen=True)
class CaptureDependencies:
    """Injectable seams for deterministic tests; defaults are production paths."""

    graph_reader: Callable[..., Any] = _default_graph_reader
    actuator: Callable[..., Mapping[str, Any]] = _default_actuator
    reconciler: Callable[..., Mapping[str, Any]] = _default_reconciler


def _safe_read_result(result: Any) -> dict[str, Any]:
    return {
        "adapter": getattr(result, "adapter", None),
        "attempted_adapters": list(getattr(result, "attempted_adapters", ()) or ()),
        "rejected_adapters": list(getattr(result, "rejected_adapters", ()) or ()),
        "observations": list(getattr(result, "observations", ()) or ()),
    }


def _operation_update_from_submission(submission: Mapping[str, Any]) -> dict[str, Any]:
    stage_times = submission.get("stage_times") if isinstance(submission.get("stage_times"), Mapping) else {}
    now = time.time()
    return {
        "authenticated_at": stage_times.get("authenticated") or (now if submission.get("authenticated") else None),
        "hydrated_at": stage_times.get("hydrated") or (now if submission.get("hydrated") else None),
        "attachment_ready_at": stage_times.get("attachment_ready"),
        "submitted_at": stage_times.get("submission_observed") or now,
        "transport": "dom",
    }


def capture_attachment_continue(
    selector: str,
    prompt: str,
    attachments: Sequence[Path | str],
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    follow: bool = False,
    dependencies: CaptureDependencies | None = None,
) -> dict[str, Any]:
    """Capture one browser attachment continuation and its canonical graph delta.

    Validation and the graph-before read happen before the actuator.  After the
    actuator is invoked exactly once, every failure is handled by read-only
    reconciliation; this function never resends the mutation.
    """

    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is empty")
    local_attachments = [LocalAttachment.from_path(path) for path in attachments]
    if not local_attachments:
        raise ValueError("attachment-continue requires at least one attachment")

    deps = dependencies or CaptureDependencies()
    conversation_id = _resolve_conversation_id(selector)
    started_at = time.time()

    # A failure here is pre-actuation and is safe for the CLI to report as an
    # ordinary nonzero validation/bootstrap failure.
    before_result = deps.graph_reader(
        conversation_id,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
    )
    graph_before = before_result.graph
    read_before = _safe_read_result(before_result)

    with MutationOperationStore(db_path) as operations:
        operation = operations.create(
            operation="attachment_continue",
            conversation_id=conversation_id,
            prompt=prompt,
        )
        operation_id = str(operation["operation_id"])
        operations.record_local_attachments(operation_id, local_attachments)
        operations.update(operation_id, state="browser_starting", transport="dom")

    actuator_attempts = 0
    protocol_observations: list[Mapping[str, Any]] = []
    capture_error: dict[str, Any] | None = None
    resolved_conversation_id = conversation_id

    try:
        actuator_attempts += 1
        submission = deps.actuator(
            selector,
            prompt,
            local_attachments,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
            follow=follow,
        )
        protocol_observations = [
            item for item in (submission.get("protocol_observations") or [])
            if isinstance(item, Mapping)
        ]
        resolved_raw = submission.get("resolved_thread_id") or submission.get("conversation_id")
        if resolved_raw:
            resolved_conversation_id = str(resolved_raw)
        update_fields = _operation_update_from_submission(submission)
        with MutationOperationStore(db_path) as operations:
            operations.update(
                operation_id,
                state="submission_observed",
                conversation_id=resolved_conversation_id,
                **update_fields,
            )
    except Exception as exc:  # noqa: BLE001 - all post-invocation failures are ambiguous.
        capture_error = {"stage": "actuator", "class": exc.__class__.__name__}
        with MutationOperationStore(db_path) as operations:
            operations.update(
                operation_id,
                state="commit_unknown",
                conversation_id=resolved_conversation_id,
                transport="dom",
                error_class=exc.__class__.__name__,
                error_detail=str(exc),
            )

    canonical_receipt: Mapping[str, Any] = {"state": "commit_unknown"}
    try:
        canonical_receipt = deps.reconciler(
            operation_id=operation_id,
            prompt=prompt,
            not_before=started_at - 5.0,
            expected_attachments=local_attachments,
            db_path=db_path,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - receipt retains class only.
        if capture_error is None:
            capture_error = {"stage": "reconcile", "class": exc.__class__.__name__}
        canonical_receipt = {"state": "commit_unknown"}
        with MutationOperationStore(db_path) as operations:
            operations.update(
                operation_id,
                state="commit_unknown",
                error_class=exc.__class__.__name__,
                error_detail=str(exc),
            )

    graph_after = None
    read_after: dict[str, Any] = {}
    try:
        after_result = deps.graph_reader(
            resolved_conversation_id,
            profile_dir=profile_dir,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            browser_timeout=browser_timeout,
        )
        graph_after = after_result.graph
        read_after = _safe_read_result(after_result)
    except Exception as exc:  # noqa: BLE001 - no retry/resend; class-only receipt.
        capture_error = {"stage": "graph_after", "class": exc.__class__.__name__}

    return build_experiment_receipt(
        operation="attachment_continue",
        actuator="browser_dom",
        conversation_id=resolved_conversation_id,
        started_at=started_at,
        completed_at=time.time(),
        local_attachments=local_attachments,
        protocol_observations=protocol_observations,
        graph_before=graph_before,
        graph_after=graph_after,
        canonical_receipt=canonical_receipt,
        read_receipts={"before": read_before, "after": read_after},
        actuator_attempts=actuator_attempts,
        capture_error=capture_error,
    )
