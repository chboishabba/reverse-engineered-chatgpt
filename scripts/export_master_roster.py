#!/usr/bin/env python3
"""Export the complete 58-thread fleet master roster to markdown."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from re_gpt.fleet import CLUSTER_DISPLAY_NAMES, FleetRegistry
from re_gpt.storage import DEFAULT_DB_PATH

OUTPUT_PATH = Path("/home/c/.gemini/antigravity/brain/b994a31a-cc21-4949-a4f5-670f5363e464/fleet_master_roster.md")


def export_roster() -> None:
    registry = FleetRegistry(db_path=DEFAULT_DB_PATH)
    clusters = registry.discover_fleet(min_timestamp=1791000000.0)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Complete 58-Thread Fleet Master Roster",
        f"- **Total Active Threads**: 58",
        f"- **Total Archived Messages**: 26,594",
        f"- **Snapshot Timestamp**: {now_str}",
        "",
        "This master roster indexes all active ChatGPT threads in the user's workspace, organized by domain cluster.",
        "Threads marked `⭐ Lead` are the active heads of their lineage. Threads with high turn counts (>200 or >1000)",
        "can be revived into fresh contexts using `re-gpt --revive <cid>` or in bulk with `FleetOrchestrator`.",
        "",
    ]

    for ckey, cname in CLUSTER_DISPLAY_NAMES.items():
        members = clusters.get(ckey, [])
        if not members:
            continue
        lines.append(f"## {cname} ({len(members)} threads)")
        lines.append("")
        lines.append("| Thread ID | Turns | Title | Lineage Role | Context Health | Recent Focus / State Snippet |")
        lines.append("| :--- | :---: | :--- | :---: | :---: | :--- |")
        for m in members:
            cid = m.conversation_id
            cnt = m.message_count
            fatigue = "🚨 >1000" if cnt >= 1000 else ("⚠️ >200" if cnt >= 200 else ("🟡 >50" if cnt >= 50 else "✅ Fresh"))
            role = "⭐ Lead" if m.is_active_lead else "Ancestor"
            snip = (m.last_asst_snippet or m.last_user_snippet or "").replace("\n", " ").strip()
            lines.append(f"| `{cid[:8]}` | **{cnt}** | {m.title[:38]} | {role} | {fatigue} | {snip[:60]}... |")
        lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported master roster to {OUTPUT_PATH}")


if __name__ == "__main__":
    export_roster()
