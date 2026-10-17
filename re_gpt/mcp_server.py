"""Native MCP surface for re-gpt.

All mutation tools call :class:`MutationService` directly. Durable operation
status comes from the shared SQLite archive; browser/DOM observation remains a
provisional diagnostic surface rather than canonical truth.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .browser_observer import observe_live_status
from .mutation_service import MutationService
from .storage import DEFAULT_DB_PATH

mcp = FastMCP("re-gpt")


def _service(db_path: str | None = None) -> MutationService:
    return MutationService(db_path=Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH)


@mcp.tool()
def chatgpt_continue(
    conversation_id: str,
    prompt: str,
    attachments: list[str] | None = None,
    follow: bool = False,
    write_transport: str = "async",
    dom_fallback: bool = True,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Continue an existing thread; direct async CFFI is primary, with safe pre-commit DOM fallback."""
    return _service(db_path).continue_thread(
        conversation_id,
        prompt,
        attachments=attachments or (),
        follow=follow,
        write_transport=write_transport,
        dom_fallback=dom_fallback,
    )


@mcp.tool()
def chatgpt_new(
    prompt: str,
    follow: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Create a new conversation through the direct async API and return its assigned conversation ID."""
    return _service(db_path).new_conversation(prompt, follow=follow)


@mcp.tool()
def chatgpt_branch(
    conversation_id: str,
    parent_node_id: str,
    prompt: str,
    attachments: list[str] | None = None,
    follow: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Create a user branch from an exact canonical graph parent node."""
    return _service(db_path).branch_from_node(
        conversation_id,
        parent_node_id,
        prompt,
        attachments=attachments or (),
        follow=follow,
    )


@mcp.tool()
def chatgpt_regenerate(
    conversation_id: str,
    user_node_id: str,
    follow: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Generate another assistant variant from an exact canonical user node."""
    return _service(db_path).regenerate(
        conversation_id,
        user_node_id,
        follow=follow,
    )


@mcp.tool()
def chatgpt_edit_branch(
    conversation_id: str,
    user_node_id: str,
    prompt: str,
    attachments: list[str] | None = None,
    follow: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Replace an exact user node's text by creating a sibling user branch."""
    return _service(db_path).edit_and_branch(
        conversation_id,
        user_node_id,
        prompt,
        attachments=attachments or (),
        follow=follow,
    )


@mcp.tool()
def chatgpt_operation_status(operation_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Read durable mutation and attachment status without launching a browser."""
    result = _service(db_path).operation_status(operation_id)
    if result is None:
        raise ValueError(f"unknown mutation operation {operation_id}")
    return result


@mcp.tool()
def chatgpt_reconcile(
    operation_id: str,
    prompt: str,
    not_before: float = 0.0,
    profile_dir: str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Reconcile a tail-continuation operation from one validated exact graph and never resubmit."""
    return _service(db_path).reconcile(
        operation_id,
        prompt=prompt,
        not_before=not_before,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
    )


@mcp.tool()
def chatgpt_live_status(
    conversation_id: str,
    profile_dir: str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Observe current rendered thread state; provisional browser evidence only."""
    return observe_live_status(
        conversation_id,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=timeout,
    )


@mcp.tool()
def chatgpt_view(conversation_id: str, db_path: str | None = None) -> dict[str, Any]:
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        conversation = connection.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if conversation is None:
            raise ValueError(f"conversation not archived: {conversation_id}")
        messages = connection.execute("""
            SELECT message_index, author, content, create_time, message_key
            FROM messages WHERE conversation_id = ? ORDER BY message_index ASC
        """, (conversation_id,)).fetchall()
        return {"conversation": dict(conversation), "messages": [dict(row) for row in messages]}
    finally:
        connection.close()


@mcp.tool()
def chatgpt_list(limit: int = 50, db_path: str | None = None) -> list[dict[str, Any]]:
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT conversation_id, title, remote_update_time, last_seen_at, cached_message_count
            FROM conversations ORDER BY COALESCE(remote_update_time, last_seen_at, 0) DESC LIMIT ?
        """, (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
