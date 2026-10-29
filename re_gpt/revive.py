"""Revive and continue long-running ChatGPT conversations with full context fidelity.

Defeats context degradation/fatigue by exporting full historical conversation
graphs from the local archive, synthesizing a structured revival memo, and
staging it as a direct attachment in a fresh conversation.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .mutation_service import MutationService
from .storage import DEFAULT_DB_PATH

PR_RE = re.compile(r"#(\d+)")
REVIVAL_DIR = Path.home() / ".cache" / "re_gpt" / "revival"


def build_revival_document(
    conversation_id: str,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    service: MutationService | None = None,
) -> tuple[Path, str]:
    """Extract conversation messages from SQLite and write a clean revival context memo."""
    REVIVAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REVIVAL_DIR / f"{conversation_id}_revival.md"

    db = Path(db_path)
    title = ""
    messages: list[tuple[int, str, str, float | None]] = []

    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT title FROM conversations WHERE conversation_id = ?", (conversation_id,))
        row = cur.fetchone()
        title = (row[0] if row and row[0] else "Untitled Conversation").strip()

        cur.execute(
            "SELECT message_index, author, content, create_time FROM messages WHERE conversation_id = ? ORDER BY message_index ASC",
            (conversation_id,),
        )
        messages = cur.fetchall()

    if not messages:
        # If no messages in local SQLite, pull the exact graph from the API
        srv = service or MutationService(db_path=db)
        pulled = srv._pull_and_persist(conversation_id)
        with sqlite3.connect(db) as conn:
            cur = conn.cursor()
            cur.execute("SELECT title FROM conversations WHERE conversation_id = ?", (conversation_id,))
            row = cur.fetchone()
            if row and row[0]:
                title = row[0].strip()
            cur.execute(
                "SELECT message_index, author, content, create_time FROM messages WHERE conversation_id = ? ORDER BY message_index ASC",
                (conversation_id,),
            )
            messages = cur.fetchall()

    # Analyze referenced PRs and paths
    all_text = " ".join(content for _, _, content, _ in messages)
    prs = sorted(set(PR_RE.findall(all_text)))

    # Automatic Semantic Rejuvenation: thread -> pnf -> world -> state capsule
    from .semantic_rejuvenation import extract_factors, state_capsule
    turns_payload = [{"message_index": idx, "content": content} for idx, _, content, _ in messages]
    factors = extract_factors(turns_payload)
    capsule_markdown = state_capsule(factors) if factors else ""

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [
        f"# Conversation Context Revival: {title}",
        f"- **Parent Conversation ID**: `{conversation_id}`",
        f"- **Total Historical Turns**: {len(messages)}",
        f"- **Extracted PNF Factors**: {len(factors)}",
        f"- **Archived & Revived At**: {now_iso}",
        "",
        "## Executive Summary & Continuity",
        "This context document captures the complete decisions, reasoning, and implementation",
        "history from the predecessor conversation, automatically synthesized via PNF factor",
        "extraction and world-model state encapsulation. The worker agent should use this memory",
        "to continue the mission with clean context and zero token degradation.",
        "",
    ]
    if capsule_markdown:
        lines.extend([
            "## Semantic Rejuvenation State Capsule (thread → pnf → world)",
            capsule_markdown.strip(),
            "",
        ])
    if prs:
        lines.extend([
            "## Referenced GitHub Pull Requests",
            ", ".join(f"#{p}" for p in prs),
            "",
        ])

    lines.extend([
        "## Conversation Transcript",
        "",
    ])

    if len(messages) > 40:
        lines.append(
            f"*(Notice: {len(messages) - 40} earlier turns compressed into semantic PNF capsule above. "
            "Displaying most recent 40 turns for continuity.)*\n"
        )
        recent_messages = messages[-40:]
    else:
        recent_messages = messages

    for idx, author, content, ctime in recent_messages:
        t_str = datetime.fromtimestamp(ctime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ctime else ""
        header = f"### Turn {idx} — {author.upper()}" + (f" ({t_str})" if t_str else "")
        lines.append(header)
        lines.append("")
        lines.append(content.strip())
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path, title


def record_lineage(
    db_path: Path | str,
    new_conversation_id: str,
    parent_conversation_id: str,
    revival_doc_path: str | None = None,
) -> None:
    """Record parent-child relationship between the original and revived conversations."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_lineage (
                conversation_id TEXT PRIMARY KEY,
                parent_conversation_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                revival_doc_path TEXT
            );
        """)
        cur.execute("""
            INSERT OR REPLACE INTO conversation_lineage (
                conversation_id, parent_conversation_id, created_at, revival_doc_path
            ) VALUES (?, ?, ?, ?)
        """, (new_conversation_id, parent_conversation_id, time.time(), revival_doc_path))
        conn.commit()


def revive_thread(
    conversation_id: str,
    prompt: str,
    *,
    attachments: Sequence[Path | str] = (),
    profile_dir: Path | str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    browser_timeout: int = 60,
    write_transport: str = "async",
    dom_fallback: bool = False,
    follow: bool = True,
    follow_timeout: float = 1800.0,
    progress_callback: Any = None,
    stream: bool = False,
    output: Path | str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    session_token: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Revive an existing long-running thread into a fresh thread with context attached."""
    service = MutationService(
        session_token=session_token,
        model=model,
        db_path=db_path,
    )

    doc_path, title = build_revival_document(conversation_id, db_path=db_path, service=service)

    init_prompt = (
        f"Initiating revived session for mission '{title}'. "
        f"Predecessor thread {conversation_id} context attached."
    )
    init_res = service.new_conversation(
        init_prompt,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        follow=False,
    )
    new_cid = str(init_res.get("conversation_id") or init_res.get("resolved_thread_id") or "").strip()
    if not new_cid:
        raise RuntimeError(f"failed to initialize new thread for revival: {init_res.get('state')}")

    record_lineage(db_path, new_cid, conversation_id, str(doc_path))

    revival_attachments = [doc_path] + [Path(p) for p in attachments]

    full_prompt = (
        f"We are reviving work from previous conversation `{conversation_id}` ('{title}').\n"
        f"Attached is `{doc_path.name}` containing the full historical decisions, PR references, and transcript.\n"
        f"Please acknowledge context from the attachment and proceed directly with the active task:\n\n"
        f"{prompt.strip()}"
    )

    if stream and progress_callback is None:
        def on_progress(event: dict[str, Any]) -> None:
            if event.get("kind") == "token_delta":
                delta = str(event.get("delta") or "")
                if delta:
                    sys.stdout.write(delta)
                    sys.stdout.flush()

        progress_callback = on_progress

    res = service.continue_thread(
        new_cid,
        full_prompt,
        attachments=revival_attachments,
        profile_dir=profile_dir,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        browser_timeout=browser_timeout,
        write_transport=write_transport,
        dom_fallback=dom_fallback,
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

    return res | {
        "parent_conversation_id": conversation_id,
        "revived_conversation_id": new_cid,
        "revival_doc_path": str(doc_path),
    }
