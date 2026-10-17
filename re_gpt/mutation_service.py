"""Reusable browser-owned mutation service.

The browser may provide submission evidence, but only the validated ChatGPT
conversation graph can certify a commit.  CLI and MCP both use this service so
there is one exactly-once mutation implementation.
"""

from __future__ import annotations

import functools
import time
from pathlib import Path
from typing import Any

from .browser_write import BrowserWriteError, submit_prompt
from .mutation_receipt import classify_prompt_mutation
from .operation_store import MutationOperationStore
from .read_plane import fetch_validated_conversation, install_validated_sync_read_plane
from .storage import ConversationStorage, DEFAULT_DB_PATH, extract_ordered_messages
from .sync_chatgpt import SyncChatGPT
from .utils import get_default_model, get_default_user_agent, get_session_token

install_validated_sync_read_plane()


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

    def _pull_and_persist(self, conversation_id: str) -> dict[str, Any]:
        token = self.session_token or get_session_token()
        default_model = self.model or get_default_model()
        user_agent = get_default_user_agent()
        with ConversationStorage(db_path=self.db_path, write_json=self.export_json) as storage, SyncChatGPT(
            session_token=token,
            default_model=default_model,
            user_agent=user_agent,
        ) as chatgpt:
            chat = fetch_validated_conversation(chatgpt, conversation_id)
            messages = extract_ordered_messages(chat)
            asset_fetcher = None
            if hasattr(chatgpt, "download_asset"):
                asset_fetcher = functools.partial(chatgpt.download_asset, conversation_id=conversation_id)
            persisted = storage.persist_chat(
                conversation_id,
                chat,
                messages,
                asset_fetcher=asset_fetcher,
            )
        return {
            "chat": chat,
            "new_messages": persisted.new_messages,
            "total_messages": persisted.total_messages,
            "json_path": str(persisted.json_path) if persisted.json_path else None,
            "asset_count": len(persisted.asset_paths),
            "asset_errors": len(persisted.asset_errors),
        }

    def reconcile(
        self,
        operation_id: str,
        *,
        prompt: str,
        not_before: float,
    ) -> dict[str, Any]:
        with MutationOperationStore(self.db_path) as operations:
            op = operations.get(operation_id)
            if not op:
                raise KeyError(f"unknown mutation operation {operation_id}")
            conversation_id = str(op.get("conversation_id") or "").strip()
            if not conversation_id:
                raise RuntimeError("operation has no conversation id to reconcile")

            pulled = self._pull_and_persist(conversation_id)
            receipt = classify_prompt_mutation(
                pulled["chat"],
                prompt,
                not_before=not_before,
            )
            if not receipt.prompt_committed:
                return operations.update(operation_id, state="commit_unknown")

            user_node_id = receipt.user_node_ids[-1]
            assistant_node_id = _assistant_child_id(pulled["chat"], user_node_id)
            now = time.time()
            state = "complete" if assistant_node_id and receipt.assistant_text else "committed_pending"
            return operations.update(
                operation_id,
                state=state,
                committed_at=now,
                completed_at=now if state == "complete" else None,
                user_node_id=user_node_id,
                assistant_node_id=assistant_node_id,
                assistant_text=receipt.assistant_text,
            ) | {
                "new_messages": pulled["new_messages"],
                "total_messages": pulled["total_messages"],
            }

    def continue_thread(
        self,
        conversation: str,
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
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        started_at = time.time()

        with MutationOperationStore(self.db_path) as operations:
            op = operations.create(operation="continue", conversation_id=conversation, prompt=prompt)
            operation_id = str(op["operation_id"])
            operations.update(operation_id, state="browser_starting")

            try:
                browser = submit_prompt(
                    conversation,
                    prompt,
                    profile_dir=profile_dir,
                    browser_mode=browser_mode,
                    cdp_endpoint=cdp_endpoint,
                    timeout=browser_timeout,
                    follow=False,
                )
            except (BrowserWriteError, OSError, ValueError) as exc:
                operations.mark_error(operation_id, "failed_precommit", exc)
                raise

            conversation_id = str(
                browser.get("resolved_thread_id") or browser.get("conversation_id") or conversation
            ).strip()
            now = time.time()
            operations.update(
                operation_id,
                state="submission_observed",
                conversation_id=conversation_id,
                authenticated_at=now if browser.get("authenticated") else None,
                hydrated_at=now if browser.get("hydrated") else None,
                submitted_at=now,
                transport="dom",
            )

        # Canonical reconciliation is outside the browser lifecycle.  Never
        # trigger the prompt again if this stage fails or remains ambiguous.
        last: dict[str, Any] | None = None
        for attempt in range(max(1, reconcile_attempts)):
            try:
                last = self.reconcile(
                    operation_id,
                    prompt=prompt,
                    not_before=started_at - 5.0,
                )
            except Exception as exc:  # mutation may already be durable remotely
                with MutationOperationStore(self.db_path) as operations:
                    last = operations.mark_error(operation_id, "commit_unknown", exc)
                break
            if last.get("state") in {"complete", "committed_pending"}:
                break
            if attempt + 1 < max(1, reconcile_attempts):
                time.sleep(max(0.0, reconcile_interval))

        if last is None:
            last = self.operation_status(operation_id) or {"operation_id": operation_id, "state": "commit_unknown"}

        if follow and last.get("state") == "committed_pending":
            deadline = time.monotonic() + max(0.0, follow_timeout)
            while time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                refreshed = self.reconcile(
                    operation_id,
                    prompt=prompt,
                    not_before=started_at - 5.0,
                )
                last = refreshed
                if refreshed.get("state") == "complete":
                    break

        return last
