"""Native MCP surface for re-gpt.

All mutation tools call :class:`MutationService` directly. Durable operation
status comes from the shared SQLite archive; browser/DOM observation remains a
provisional diagnostic surface rather than canonical truth.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .adapter_health import AdapterHealthStore
from .async_bridge import run_async_sync
from .async_chatgpt import AsyncChatGPT
from .browser_session_replay import load_browser_session_snapshot
from .browser_observer import observe_live_status
from .mutation_service import MutationService
from .operation_store import MutationOperationStore
from .protocol_drift import build_protocol_fingerprint, compare_protocol_fingerprints
from .session_lifecycle import SessionLifecycle
from .storage import DEFAULT_DB_PATH, ConversationStorage
from .utils import get_default_model, get_default_user_agent, get_session_token

mcp = FastMCP("re-gpt")

WORKER_PROMPT_PRESETS: dict[str, str] = {
    "max_cut": (
        "Exactly complete all per max-cut and continue per pareto/snowball/ibrahim. "
        "Check the web and latest PRs for inspiration and cross-pollination -- assume we "
        "already have everything we need in-repo. Keep following your nose until you hit "
        "a wall -- do not wait for approval, and remember our attribution rules."
    ),
    "grug_ste100": (
        "Apply Grug-brain simplicity and ASD-STE100 Simplified Technical English: one "
        "thought per sentence, active voice, approved unambiguous terms, zero speculative "
        "abstraction, no inline code dumps in prose, and normalize onto existing in-repo surfaces."
    ),
    "grug": (
        "Apply Grug-brain simplicity and ASD-STE100 Simplified Technical English: one "
        "thought per sentence, active voice, approved unambiguous terms, zero speculative "
        "abstraction, no inline code dumps in prose, and normalize onto existing in-repo surfaces."
    ),
    "zkp_qa": (
        "Frame via ZKP (O/R/C/S/L/P/G/F) with explicit preconditions, invariants, and "
        "postconditions. Converge toward normalized surfaces per ITIL, ISO 9000/42001/27001/"
        "27701/23894/9241, NIST AI RMF, Six Sigma, and C4/PlantUML. Assign one disjoint "
        "nonblocking lane per worker, no skeletons or stubs, and complete implementation + "
        "deterministic verification within the turn."
    ),
    "zkp": (
        "Frame via ZKP (O/R/C/S/L/P/G/F) with explicit preconditions, invariants, and "
        "postconditions. Converge toward normalized surfaces per ITIL, ISO 9000/42001/27001/"
        "27701/23894/9241, NIST AI RMF, Six Sigma, and C4/PlantUML. Assign one disjoint "
        "nonblocking lane per worker, no skeletons or stubs, and complete implementation + "
        "deterministic verification within the turn."
    ),
    "introspective_viz": (
        "Apply the introspective method: 1. Model the theory accurately per actual "
        "implementation. 2. Generate a plain-language, physically intuitive visual proof "
        "(Python/HTML/SVG). 3. Inspect the visualization. 4. Formalize new findings in "
        "Lean or Agda per repo convention."
    ),
    "converge_toe": (
        "Converge toward the DASHI TOE: dashi_agda DASHI/EverythingTOESymmetryResolved369.agda, "
        "DASHI/Unifier.agda and DASHI/Unified/. Assume the content already exists in-repo. "
        "Search first for existing, duplicate or near constructions (GitHub code search; the local "
        "referee confirms with dashi-agda-preflight related_symbols/discover/round_lineage). "
        "Order: reuse, then unify duplicates, then extend, then construct new. Attach every new "
        "result to the TOE import graph. Do not edit ESD surfaces (dashi_digital_esd repo, "
        "*digital_esd* and *ESD* modules); import only. Keep attribution for meta-introspector repos."
    ),
    "referee_pass": (
        "Local referee verification passed (0 errors, 0 warnings). Proceed immediately "
        "to the next highest-alpha vertical slice per max-cut and pareto/snowball/ibrahim; "
        "push commits and return SHAs + deterministic referee commands."
    ),
}

_VERBOSE_OPERATION_KEYS = {
    "read_diagnostic",
    "write_diagnostic",
    "read_diagnostics",
    "read_observations",
    "write_observations",
    "started_at",
    "authenticated_at",
    "hydrated_at",
    "attachment_ready_at",
    "submitted_at",
    "committed_at",
    "completed_at",
    "requested_parent_node_id",
    "attachment_ids",
    "attachments",
    "attempted_read_adapters",
    "rejected_read_adapters",
    "prompt_digest",
    "transport",
    "operation",
    "user_node_id",
    "assistant_node_id",
    "read_adapter",
    "new_messages",
    "total_messages",
}


def expand_worker_prompt(prompt: str, preset: str | None = None) -> str:
    """Expand leading @preset tokens and an optional preset key into worker instructions."""
    parts: list[str] = []
    if preset:
        for key in preset.replace(",", " ").split():
            clean = key.lstrip("@").strip()
            if clean not in WORKER_PROMPT_PRESETS:
                raise ValueError(f"Unknown worker prompt preset: {clean}")
            parts.append(WORKER_PROMPT_PRESETS[clean])
    text = (prompt or "").strip()
    if text:
        tokens = text.split()
        consumed = 0
        for tok in tokens:
            if tok.startswith("@") and tok[1:] in WORKER_PROMPT_PRESETS:
                parts.append(WORKER_PROMPT_PRESETS[tok[1:]])
                consumed += 1
            else:
                break
        remainder = "" if consumed == len(tokens) else (text.split(None, consumed)[-1] if consumed else text)
        if remainder:
            parts.append(remainder)
    resolved = "\n\n".join(p for p in parts if p)
    if not resolved:
        raise ValueError("Worker prompt must be non-empty after preset expansion")
    return resolved


def compact_operation_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return {
            k: v
            for k, v in result.items()
            if k not in _VERBOSE_OPERATION_KEYS
            and not (k in {"error_class", "error_detail"} and v is None)
        }
    return result


def _service(db_path: str | None = None) -> MutationService:
    return MutationService(db_path=Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH)

def _normalized_timestamp(value: Any) -> float:
    """Normalize legacy numeric or ISO-8601 SQLite timestamps to epoch seconds."""
    if value is None:
        return float("-inf")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return float("-inf")
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _unhydrated_targets_by_recency(
    db_path: Path,
    *,
    limit: int,
) -> list[tuple[str, str]]:
    cap = max(1, min(int(limit), 20))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.conversation_id, c.title, c.remote_update_time, c.last_seen_at
            FROM conversations c
            LEFT JOIN messages m ON c.conversation_id = m.conversation_id
            GROUP BY c.conversation_id
            HAVING count(m.message_index) = 0
            """
        ).fetchall()
    ranked = sorted(
        rows,
        key=lambda row: (
            max(_normalized_timestamp(row[2]), _normalized_timestamp(row[3])),
            str(row[0]),
        ),
        reverse=True,
    )
    return [(str(row[0]), str(row[1] or row[0])) for row in ranked[:cap]]


def _sync_roster_via_async_cffi(
    *,
    db_path: Path,
    page_limit: int = 28,
    max_conversations: int = 500,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        try:
            token = get_session_token()
        except Exception:
            token = None
        snapshot = load_browser_session_snapshot(max_age_seconds=3600)
        async with AsyncChatGPT(
            session_token=token,
            default_model=get_default_model(),
            user_agent=get_default_user_agent(),
            browser_session=snapshot,
            auto_check_websocket=False,
        ) as client:
            conversations: list[dict[str, Any]] = []
            offset = 0
            page_size = max(1, min(int(page_limit), 100))
            cap = max(1, min(int(max_conversations), 5000))
            while len(conversations) < cap:
                requested = min(page_size, cap - len(conversations))
                page = await client.list_conversations_page(
                    offset=offset,
                    limit=requested,
                )
                items = page.get("items", [])
                if not isinstance(items, list):
                    raise ValueError("conversation roster response has non-list items")
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    conversation_id = str(item.get("id") or "").strip()
                    if not conversation_id:
                        continue
                    conversations.append(
                        {
                            "id": conversation_id,
                            "title": item.get("title"),
                            "update_time": item.get("update_time"),
                        }
                    )
                if len(items) < requested:
                    break
                offset += len(items)
                if not items:
                    break

        with ConversationStorage(db_path=db_path) as storage:
            stats = storage.record_conversations(conversations)
        return {
            "transport": "async_curl_cffi",
            "conversation_count": len(conversations),
            "added": stats.added,
            "updated": stats.updated,
            "browser_fallback": False,
            "sync_fallback": False,
        }

    return run_async_sync(run)



@mcp.tool()
def chatgpt_continue(
    conversation_id: str,
    prompt: str,
    attachments: list[str] | None = None,
    follow: bool = False,
    write_transport: str = "async",
    dom_fallback: bool = False,
    db_path: str | None = None,
    preset: str | None = None,
    auto_latex_bridge: bool = False,
    pnf_mode: bool = False,
    reconcile_attempts: int = 3,
) -> dict[str, Any]:
    """Continue via direct async CFFI; DOM fallback is explicit diagnostic opt-in only."""
    resolved_prompt = expand_worker_prompt(prompt, preset=preset)
    bridge_payload: dict[str, Any] | None = None

    if pnf_mode or auto_latex_bridge:
        try:
            from itir_mcp.latex_math_bridge import distill_pnf_capsule
            capsule = distill_pnf_capsule(resolved_prompt, conversation_id=conversation_id)
            bridge_payload = capsule.to_dict()
            if pnf_mode:
                resolved_prompt = capsule.condensed_prompt_text
        except Exception:
            pass

    result = _service(db_path).continue_thread(
        conversation_id,
        resolved_prompt,
        attachments=attachments or (),
        follow=follow,
        write_transport=write_transport,
        dom_fallback=dom_fallback,
        reconcile_attempts=reconcile_attempts,
    )
    compacted = compact_operation_payload(result)
    if bridge_payload is not None:
        compacted["latex_bridge"] = bridge_payload
    return compacted


@mcp.tool()
def chatgpt_new(prompt: str, follow: bool = False, db_path: str | None = None) -> dict[str, Any]:
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
        conversation_id, parent_node_id, prompt, attachments=attachments or (), follow=follow,
    )


@mcp.tool()
def chatgpt_regenerate(
    conversation_id: str,
    user_node_id: str,
    follow: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Generate another assistant variant from an exact canonical user node."""
    return _service(db_path).regenerate(conversation_id, user_node_id, follow=follow)


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
        conversation_id, user_node_id, prompt, attachments=attachments or (), follow=follow,
    )


@mcp.tool()
def chatgpt_session_status(profile_dir: str | None = None, snapshot_path: str | None = None) -> dict[str, Any]:
    """Return redacted local session freshness/expiry state without exposing token or cookie values."""
    return SessionLifecycle().status(profile_dir=profile_dir, snapshot_path=snapshot_path)


@mcp.tool()
def chatgpt_session_bootstrap(
    profile_dir: str | None = None,
    snapshot_path: str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Capture fresh owned browser session state from the lightweight ChatGPT root context."""
    return SessionLifecycle().bootstrap(
        profile_dir=profile_dir,
        snapshot_path=snapshot_path,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=timeout,
    )


@mcp.tool()
def chatgpt_session_refresh(
    profile_dir: str | None = None,
    snapshot_path: str | None = None,
    browser_mode: str = "displayless",
    cdp_endpoint: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Refresh persisted owned browser session state and return only a redacted status receipt."""
    return SessionLifecycle().refresh(
        profile_dir=profile_dir,
        snapshot_path=snapshot_path,
        browser_mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        timeout=timeout,
    )


@mcp.tool()
def chatgpt_session_doctor(profile_dir: str | None = None, snapshot_path: str | None = None) -> dict[str, Any]:
    """Classify session failure mode and recommend bootstrap/refresh actions without secrets."""
    return SessionLifecycle().doctor(profile_dir=profile_dir, snapshot_path=snapshot_path)


@mcp.tool()
def chatgpt_protocol_fingerprint(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a value-free structural protocol fingerprint from redacted observations."""
    return build_protocol_fingerprint(observations)


@mcp.tool()
def chatgpt_protocol_compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two structural fingerprints and classify compatible/drift_suspected/incompatible."""
    return compare_protocol_fingerprints(baseline, candidate)


@mcp.tool()
def chatgpt_adapter_health(db_path: str | None = None, sample_limit: int = 200) -> dict[str, Any]:
    """Return persistent exact-read adapter health, cooldown and empirical latency telemetry."""
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    with AdapterHealthStore(path) as store:
        return store.report(sample_limit=max(1, min(int(sample_limit), 2000)))


@mcp.tool()
def chatgpt_operation_status(operation_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Read durable mutation and attachment status without launching a browser."""
    result = _service(db_path).operation_status(operation_id)
    if result is None:
        raise ValueError(f"unknown mutation operation {operation_id}")
    return result


@mcp.tool()
def chatgpt_live_activity(operation_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Read recent SSE activity without exposing reasoning text or tool arguments."""
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    with MutationOperationStore(path) as store:
        result = store.live_activity(operation_id)
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
def chatgpt_view(
    conversation_id: str,
    tail: int = 5,
    message_index: int | None = None,
    author: str | None = None,
    max_chars: int = 0,
    db_path: str | None = None,
) -> dict[str, Any]:
    """View conversation metadata and recent/filtered turns (token-optimized). Set tail=0 for all."""
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        conversation = connection.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if conversation is None:
            raise ValueError(f"conversation not archived: {conversation_id}")
        query = """
            SELECT message_index, author, content, create_time, message_key
            FROM messages WHERE conversation_id = ? ORDER BY message_index ASC
        """
        all_messages = [dict(row) for row in connection.execute(query, (conversation_id,)).fetchall()]
        total_messages = len(all_messages)
        messages = all_messages
        if message_index is not None:
            messages = [m for m in messages if m.get("message_index") == int(message_index)]
        else:
            if author and author.strip():
                role = author.strip().lower()
                messages = [m for m in messages if str(m.get("author") or "").lower() == role]
            if tail > 0 and len(messages) > tail:
                messages = messages[-tail:]
        if max_chars and int(max_chars) > 0:
            limit_c = int(max_chars)
            for m in messages:
                text = str(m.get("content") or "")
                if len(text) > limit_c:
                    m["content"] = text[:limit_c] + f"... [+{len(text) - limit_c} chars]"
        return {
            "conversation": {
                "conversation_id": conversation["conversation_id"],
                "title": conversation["title"],
                "remote_update_time": conversation["remote_update_time"],
            },
            "total_messages": total_messages,
            "returned_messages": len(messages),
            "messages": messages,
        }
    finally:
        connection.close()


@mcp.tool()
def chatgpt_sync_roster(
    page_limit: int = 28,
    max_conversations: int = 500,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Refresh the local conversation roster through authenticated async curl_cffi only."""
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    return _sync_roster_via_async_cffi(
        db_path=path,
        page_limit=page_limit,
        max_conversations=max_conversations,
    )


@mcp.tool()
def chatgpt_hydrate(
    conversation_id: str | None = None,
    limit: int = 1,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Hydrate message graphs into local SQLite using direct async CFFI (token-optimized)."""
    service = _service(db_path)
    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    
    target_ids: list[tuple[str, str]] = []
    if conversation_id and conversation_id.strip():
        cid = conversation_id.strip()
        with sqlite3.connect(path) as conn:
            cur = conn.cursor()
            title = cur.execute("SELECT title FROM conversations WHERE conversation_id = ?", (cid,)).fetchone()
            target_ids.append((cid, (title[0] if title else cid)))
    else:
        target_ids.extend(_unhydrated_targets_by_recency(path, limit=limit))

    if not target_ids:
        return {"hydrated_count": 0, "results": [], "status": "no_unhydrated_targets"}

    results = []
    for cid, title in target_ids:
        try:
            res = service._pull_and_persist(cid, browser_fallback=False)
            results.append({
                "conversation_id": cid,
                "title": (title or cid).strip()[:40],
                "messages": res.get("total_messages", 0),
                "adapter": res.get("read_adapter", "unknown"),
                "status": "ok",
            })
        except Exception as exc:
            results.append({
                "conversation_id": cid,
                "title": (title or cid).strip()[:40],
                "error": str(exc),
                "status": "error",
            })

    return {
        "hydrated_count": len([r for r in results if r.get("status") == "ok"]),
        "results": results,
    }


@mcp.tool()
def chatgpt_revive(
    conversation_id: str,
    prompt: str,
    attachments: list[str] | None = None,
    follow: bool = False,
    output: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Revive an existing long-running thread into a fresh thread with clean context and recorded lineage."""
    from .revive import revive_thread

    resolved_db = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    res = revive_thread(
        conversation_id=conversation_id,
        prompt=prompt,
        attachments=[Path(p) for p in (attachments or ())],
        follow=follow,
        output=Path(output) if output else None,
        db_path=resolved_db,
    )
    return compact_operation_payload(res)


@mcp.tool()
def chatgpt_list(
    limit: int = 20,
    query: str | None = None,
    keywords: list[str] | None = None,
    order_by: str = "recent",
    min_turns: int = 0,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """List or search archived conversations by query or multi-keywords, ordered by recency or turns."""
    from .fleet import classify_text

    path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        cap = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []

        if query and query.strip():
            clauses.append("LOWER(COALESCE(title, '')) LIKE ?")
            params.append(f"%{query.strip().lower()}%")

        if keywords:
            clean_kws = [k.strip().lower() for k in keywords if k.strip()]
            if clean_kws:
                kw_clauses = ["LOWER(COALESCE(title, '')) LIKE ?" for _ in clean_kws]
                clauses.append(f"({' OR '.join(kw_clauses)})")
                params.extend(f"%{kw}%" for kw in clean_kws)

        if min_turns > 0:
            clauses.append("COALESCE(cached_message_count, 0) >= ?")
            params.append(min_turns)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_col = (
            "COALESCE(cached_message_count, 0) DESC"
            if order_by == "turns"
            else "COALESCE(remote_update_time, last_seen_at, 0) DESC"
        )
        sql = f"""
            SELECT conversation_id, title, remote_update_time, last_seen_at, cached_message_count
            FROM conversations
            {where_sql}
            ORDER BY {order_col} LIMIT ?
        """
        params.append(cap)
        rows = connection.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["cluster"] = classify_text(d.get("title") or "")
            d["turns"] = d.get("cached_message_count") or 0
            results.append(d)
        return results
    finally:
        connection.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
