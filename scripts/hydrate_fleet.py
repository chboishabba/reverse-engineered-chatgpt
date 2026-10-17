#!/usr/bin/env python3
"""Hydrate full message graphs for recent unpulled threads in local SQLite."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from re_gpt.mutation_service import MutationService
from re_gpt.storage import DEFAULT_DB_PATH

PROFILE_DIR = Path("/home/c/Documents/code/ITIR-suite/reverse-engineered-chatgpt/chatgpt_browser_profile")


def hydrate_recent(min_timestamp: float = 1791000000.0, limit: int = 60) -> None:
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.conversation_id, c.title
        FROM conversations c
        LEFT JOIN messages m ON c.conversation_id = m.conversation_id
        WHERE CAST(c.remote_update_time AS REAL) >= ?
        GROUP BY c.conversation_id
        HAVING count(m.message_index) = 0
        ORDER BY CAST(c.remote_update_time AS REAL) DESC
        LIMIT ?
    """, (min_timestamp, limit))
    unpulled = cur.fetchall()
    conn.close()

    print(f"Starting hydration of {len(unpulled)} recent threads...")
    service = MutationService()

    success = 0
    errors = 0
    for i, (cid, title) in enumerate(unpulled, 1):
        print(f"[{i}/{len(unpulled)}] Hydrating {cid[:8]} ({title.strip()[:35]})...", flush=True)
        try:
            res = service._pull_and_persist(cid, profile_dir=PROFILE_DIR)
            msgs = res.get("total_messages", 0)
            print(f" -> {msgs} messages hydrated", flush=True)
            success += 1
        except Exception as exc:
            print(f" -> ERROR: {exc}", file=sys.stderr, flush=True)
            errors += 1
        time.sleep(1.2)  # respectful pacing between exact graph pulls

    print(f"Hydration complete: {success} succeeded, {errors} errors.")


if __name__ == "__main__":
    hydrate_recent()
