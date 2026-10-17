#!/usr/bin/env python3
"""Analyze the exact 36 browser tabs against SQLite and cluster/dedupe them."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

db = Path.home() / ".chatgpt_history.sqlite3"
conn = sqlite3.connect(db)
cur = conn.cursor()

raw_threads = [
    ("6abb95db-ab6c-83ec-b587-f997b257ca43", "Compare NetDocuments Actionstep"),
    ("6ac5c04a-68cc-83ec-a235-39671c15f0c0", "CLI ChatGPT Access History"),
    ("6ab664a6-6518-83ec-bd82-b8a374261538", "Formalise Nongin Content"),
    ("6ab64e16-a4e0-83ec-94a7-0bb99bff501c", "Formalise Psychological Warfare Detection"),
    ("6ab71104-acbc-83ec-9ee0-af42aa6f33ad", "Formalise Plantation To Paradise"),
    ("6ab9a939-c734-83ec-8c74-0cbc51c237cd", "Berkeley Diffraction Imaging"),
    ("6ac6dc29-1d08-83ec-990e-a1ca076ccc3d", "Branch · Check Antigravity PRs"),
    ("6abb1838-01a4-83ec-bada-41cb0168b63c", "SpaceX Investment Analysis"),
    ("6abb9597-f33c-83ec-aa20-c5cba53a8a8e", "Formalise Spintronics Prior Art"),
    ("6abb8aa5-a018-83ec-96e5-568b444c2dfe", "Formalise Rocketdyne Research"),
    ("6abf28f9-af5c-83ec-bfa6-4a932cd7124d", "Check repository 279 occurrences"),
    ("local-chatgpt:23544cd5-2433-4640-ac57-9d8a1bbf3b75", "Branch · Formalise IRGC Letter Analysis"),
    ("6abf52bb-d584-83ec-9c8f-cc33ba859b7b", "Formalise Ye Redshift Result"),
    ("6ac03af0-ad38-83ec-9629-16b17f6f5686", "Fact check transcript"),
    ("6ac187ff-92bc-83ec-b495-6a930a1fae48", "Check Vs SOTA"),
    ("6ac2f98d-29f0-83ec-a4fd-9eaffc9f93a4", "Formalise Psychoanalysis Model"),
    ("6ac46575-9028-83ec-9851-92812bc54b12", "Branch · Compare E8 Models"),
    ("6ac5787b-3264-83ec-b243-24a6b559b6f0", "Review AI Bubble Content"),
    ("6ac326ef-28a4-83ec-9f47-39a396290745", "Unpack geometric reasoning"),
    ("6ac5af83-ee64-83ec-abdb-37195040cd5a", "Branch · Branch · Lean Millennium Problems Repo"),
    ("6ac32646-0abc-83ec-93ce-cc4cb41858d0", "Formalise Framework Layers"),
    ("6ac5b02d-0620-83ec-bdb8-ed00c7bc04e6", "Branch · Repo says Collatz"),
    ("local-chatgpt:f37d877b-0b71-4c43-87f1-2ccaae1b8e2a", "Branch · Antigravity Content Analysis"),
    ("6ac5afce-11ec-83ec-92fa-7ef25d513fa0", "Branch · Formalise Framework Layers"),
    ("6ac5af06-6c28-83ec-a9bf-49c751d1eeaa", "Branch · Compare E8 Models"),
    ("6abf3cd7-b3a4-83ec-b394-53bcc69f452d", "Branch · Programme Status Summary"),
    ("6ac5b4ab-16dc-83ec-a9a9-3cecf6b39314", "Generate field graph"),
    ("6ac2ff3d-2964-83ec-b270-49fbf8543089", "Formalise Governance Model"),
    ("6ac4642a-99ec-83ec-a487-edc97e1b5ac5", "Formalise Tokamak Stellarator"),
    ("6ab86da0-662c-83ec-89f1-05a11e170de0", "Explain Quantum Mereology"),
    ("6acc421d-e020-83ec-9919-1b72208358a1", "Formalise Radix Prefix Tree"),
    ("6acc67a2-d538-83ec-8adf-e692f2ced8d9", "Explore Ogg Prime Fourier Geometry"),
    ("6acc80dc-6f2c-83ec-961b-0df581615fcc", "Review Eisenstein Content"),
    ("6acecb94-b3d0-83ec-a2c7-f6ba29f6ee65", "Repo progress check"),
    ("6acef158-d424-83ec-8d63-e10e99fdc451", "Review Open Questions"),
    ("6acef5b5-33a4-83ec-9be6-e5d785138d64", "Assess repo reuse direction"),
]

PR_RE = re.compile(r"#(\d+)")

records = []
for cid, title in raw_threads:
    cur.execute("SELECT count(*) FROM messages WHERE conversation_id = ? OR conversation_id LIKE ?", (cid, f"{cid}%"))
    msg_cnt = cur.fetchone()[0]

    cur.execute("SELECT content FROM messages WHERE conversation_id = ? OR conversation_id LIKE ?", (cid, f"{cid}%"))
    full_text = " ".join(r[0] for r in cur.fetchall() if r[0])
    prs = sorted(set(int(p) for p in PR_RE.findall(full_text) if 10 < int(p) < 2000))

    cur.execute("SELECT content FROM messages WHERE conversation_id = ? OR conversation_id LIKE ? ORDER BY message_index DESC LIMIT 1", (cid, f"{cid}%"))
    row = cur.fetchone()
    last_msg = row[0][:80].replace("\n", " ") if row else ""

    records.append({
        "cid": cid,
        "title": title,
        "turns": msg_cnt,
        "prs": prs,
        "last_msg": last_msg,
    })

print(f"Total Browser Tabs Processed: {len(records)}\n")
for r in records:
    c_short = r["cid"][:10]
    t = r["title"][:34]
    turns = r["turns"]
    prs_str = ", ".join(f"#{p}" for p in r["prs"][:3])
    if len(r["prs"]) > 3:
        prs_str += "..."
    print(f"{c_short:10s} | {turns:4d} turns | {t:34s} | PRs: {prs_str}")
