#!/usr/bin/env python3
"""Scan recent threads in SQLite for high-alpha mathematical, physical, and foundational breakthroughs."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

db = Path.home() / ".chatgpt_history.sqlite3"
conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute("""
    SELECT conversation_id, title, CAST(remote_update_time AS REAL) as ts, cached_message_count
    FROM conversations
    ORDER BY ts DESC
    LIMIT 300
""")
rows = cur.fetchall()

keywords = [
    "clay", "goldbach", "riemann", "rh", "navier", "stokes", "ns", "yang", "mills", "ym",
    "mass gap", "monster", "moonshine", "e8", "f4", "albert", "jack daniels",
    "fractran", "prime", "ogg", "casimir", "wec", "mec", "redshift", "superconductor",
    "tokamak", "stellarator", "soliton", "vortex", "hodge", "poincare", "bsd",
    "p vs np", "max cut", "cffi", "mereology", "sensiblaw", "teleodynamic", "nongin",
    "collatz", "quantum", "graphene", "uniprot", "c3", "toe", "spin", "dirac", "hilbert"
]

high_alpha = []
for cid, title, ts, cached_cnt in rows:
    t_clean = (title or "").strip()
    t_lower = t_clean.lower()

    cur.execute("SELECT count(*) FROM messages WHERE conversation_id = ?", (cid,))
    msg_cnt = cur.fetchone()[0]
    effective_cnt = max(msg_cnt, cached_cnt or 0)

    # Check title keywords
    matched_kws = [kw for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", t_lower)]

    # Also check content if available
    cur.execute("SELECT content FROM messages WHERE conversation_id = ? ORDER BY message_index DESC LIMIT 3", (cid,))
    recent_msgs = cur.fetchall()
    content_sample = " ".join(r[0] for r in recent_msgs if r[0]).lower()

    # Content-based high alpha checks
    for extra_kw in ["clay millennium", "goldbach", "navier-stokes", "mass gap", "monster moonshine", "fractran", "riemann hypothesis"]:
        if extra_kw in content_sample and extra_kw not in matched_kws:
            matched_kws.append(extra_kw)

    if matched_kws:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "n/a"
        high_alpha.append({
            "cid": cid,
            "title": t_clean,
            "date": dt,
            "turns": effective_cnt,
            "keywords": matched_kws,
            "content_sample": content_sample[:160].replace("\n", " ").strip(),
        })

print(f"Total High-Alpha candidate threads found: {len(high_alpha)}\n")
for h in high_alpha:
    c = h["cid"][:8]
    d = h["date"]
    t = h["turns"]
    title = h["title"][:42]
    k = ", ".join(h["keywords"][:4])
    print(f"{c:8s} | {d:10s} | {t:4d} turns | {title:42s} | [{k}]")
