#!/usr/bin/env python3
"""Survey the recent ~60 threads, cluster them by domain, and output an aligned task matrix."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from re_gpt.storage import DEFAULT_DB_PATH

PR_RE = re.compile(r"#(\d+)")

CLUSTERS = {
    "DASHI / Physics / Yang-Mills / Agda": [
        "round", "balaban", "yangmills", "yang-mills", "dashi", "c3", "uniprot",
        "casimir", "tokamak", "stellarator", "ye redshift", "nongin", "plantation",
        "pccs", "agda", "formalise", "wec", "mec", "violation", "su(n)", "erdős", "trauma"
    ],
    "Max Cut / Partitioning": [
        "max cut", "maxcut", "partition", "cffi", "lean max cut"
    ],
    "SensibLaw / Legal / Governance": [
        "sensiblaw", "legal", "actionstep", "netdocuments", "pareto", "aml",
        "ethics", "compliance", "law", "egress"
    ],
    "Infrastructure / Tooling": [
        "sqlite", "chatgpt access history", "re-gpt", "roadmap", "mcp", "repo state", "compare two prs", "collatz"
    ],
}


def classify(title: str, user_text: str, asst_text: str) -> str:
    combined = f"{title} {user_text} {asst_text}".lower()
    for cat, kws in CLUSTERS.items():
        if any(kw in combined for kw in kws):
            return cat
    return "Research & General"


def generate_survey() -> dict[str, Any]:
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            conversation_id, 
            title, 
            CAST(remote_update_time AS REAL) as ts,
            cached_message_count
        FROM conversations 
        WHERE CAST(remote_update_time AS REAL) >= 1791000000
        ORDER BY ts DESC
    """)
    rows = cur.fetchall()

    items = []
    for cid, title, ts, cached_count in rows:
        title = (title or "(no title)").strip()
        dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "n/a"

        # Count actual messages in DB
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id = ?", (cid,))
        msg_count = cur.fetchone()[0]

        # Get last user message
        cur.execute("""
            SELECT content FROM messages 
            WHERE conversation_id = ? AND author = 'user' 
            ORDER BY message_index DESC LIMIT 1
        """, (cid,))
        u_row = cur.fetchone()
        u_text = u_row[0] if u_row else ""

        # Get last assistant message
        cur.execute("""
            SELECT content FROM messages 
            WHERE conversation_id = ? AND author = 'assistant' 
            ORDER BY message_index DESC LIMIT 1
        """, (cid,))
        a_row = cur.fetchone()
        a_text = a_row[0] if a_row else ""

        full_text = f"{title} {u_text} {a_text}"
        prs = sorted(set(PR_RE.findall(full_text)))

        cluster = classify(title, u_text, a_text)

        items.append({
            "id": cid,
            "title": title,
            "updated_at": dt_str,
            "timestamp": ts,
            "message_count": msg_count,
            "cluster": cluster,
            "referenced_prs": [f"#{p}" for p in prs],
            "last_user_snippet": u_text[:140].replace("\n", " ").strip(),
            "last_asst_snippet": a_text[:140].replace("\n", " ").strip(),
        })

    conn.close()
    return {"total": len(items), "threads": items}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Survey recent threads from local SQLite archive.")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path; defaults to stdout if not set.")
    parser.add_argument("--limit", "-n", type=int, default=100, help="Maximum threads to include.")
    args = parser.parse_args()

    data = generate_survey()
    formatted = json.dumps(data, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(formatted, encoding="utf-8")
        print(f"Generated survey for {data['total']} threads -> {args.output}")
    else:
        print(formatted)

