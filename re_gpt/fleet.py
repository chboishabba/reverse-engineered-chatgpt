"""Fleet Orchestrator for managing, synchronizing, and coordinating multi-thread ChatGPT workflows.

Allows an agent to orchestrate the entire fleet of ~50-70 threads simultaneously:
- Catalogs threads by domain cluster (Yang-Mills/Agda, Max Cut, SensibLaw, Infra, Research).
- Tracks lineage (parent -> revived child) to maintain active working leads.
- Automatically revives fatigued threads (>80-100 turns) with zero context loss.
- Dispatches tasks concurrently across domain worker threads, paced safely by the global rate limiter.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .mutation_service import MutationService
from .revive import revive_thread
from .storage import DEFAULT_DB_PATH

PR_RE = re.compile(r"#(\d+)")

CLUSTER_PATTERNS: dict[str, list[str]] = {
    "sensiblaw": [
        "sensiblaw", "legal", "actionstep", "netdocuments", "pareto", "aml",
        "ethics", "compliance", "law", "egress"
    ],
    "max_cut": [
        "max cut", "maxcut", "partition", "cffi", "lean max cut"
    ],
    "infrastructure": [
        "sqlite", "chatgpt access history", "re-gpt", "roadmap", "mcp", "repo state", "compare two prs", "collatz"
    ],
    "yang_mills": [
        "round", "balaban", "yangmills", "yang-mills", "dashi", "c3", "uniprot",
        "casimir", "tokamak", "stellarator", "ye redshift", "nongin", "plantation",
        "pccs", "agda", "formalise", "wec", "mec", "violation", "su(n)", "erdős", "trauma"
    ],
}

CLUSTER_DISPLAY_NAMES: dict[str, str] = {
    "yang_mills": "DASHI / Physics / Yang-Mills / Agda",
    "max_cut": "Max Cut / Partitioning",
    "sensiblaw": "SensibLaw / Legal / Governance",
    "infrastructure": "Infrastructure / Tooling",
    "research": "Research & Derivations",
}


def classify_text(text: str, title: str = "") -> str:
    cleaned = re.sub(r"\[re-gpt\][^\n]+", "", text, flags=re.IGNORECASE).lower()
    if title:
        t_lower = title.lower()
        for cluster_key, keywords in CLUSTER_PATTERNS.items():
            if any(kw in t_lower for kw in keywords):
                return cluster_key
    for cluster_key, keywords in CLUSTER_PATTERNS.items():
        if any(kw in cleaned for kw in keywords):
            return cluster_key
    return "research"


@dataclass
class FleetMember:
    conversation_id: str
    title: str
    cluster: str
    cluster_name: str
    message_count: int
    remote_update_time: float
    updated_at_str: str
    parent_conversation_id: str | None = None
    revived_child_id: str | None = None
    is_active_lead: bool = False
    referenced_prs: list[str] | None = None
    last_user_snippet: str = ""
    last_asst_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FleetRegistry:
    """Discovers, tracks, and manages domain worker threads in local SQLite."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).expanduser()

    def discover_fleet(self, min_timestamp: float = 1791000000.0) -> dict[str, list[FleetMember]]:
        """Scan conversations, lineages, and messages into clustered FleetMembers."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()

            # Ensure lineage table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_lineage (
                    conversation_id TEXT PRIMARY KEY,
                    parent_conversation_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    revival_doc_path TEXT
                );
            """)

            # Fetch lineages
            cur.execute("SELECT conversation_id, parent_conversation_id FROM conversation_lineage")
            parent_to_child: dict[str, str] = {}
            child_to_parent: dict[str, str] = {}
            for child, parent in cur.fetchall():
                child_to_parent[child] = parent
                parent_to_child[parent] = child

            # Fetch active conversations
            cur.execute("""
                SELECT 
                    conversation_id, 
                    title, 
                    CAST(remote_update_time AS REAL) as ts
                FROM conversations 
                WHERE CAST(remote_update_time AS REAL) >= ?
                ORDER BY ts DESC
            """, (min_timestamp,))
            rows = cur.fetchall()

            # For each conversation, query count & last snippets
            clusters: dict[str, list[FleetMember]] = {k: [] for k in CLUSTER_DISPLAY_NAMES}

            for cid, title, ts in rows:
                title = (title or "(no title)").strip()
                dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "n/a"

                cur.execute("SELECT count(*) FROM messages WHERE conversation_id = ?", (cid,))
                msg_count = cur.fetchone()[0]

                cur.execute("""
                    SELECT author, content FROM messages 
                    WHERE conversation_id = ? 
                    ORDER BY message_index DESC LIMIT 2
                """, (cid,))
                recent = cur.fetchall()
                u_text = next((c for a, c in recent if a == "user"), "")
                a_text = next((c for a, c in recent if a == "assistant"), "")

                full_text = f"{title} {u_text} {a_text}"
                prs = sorted(set(PR_RE.findall(full_text)))
                cluster_key = classify_text(full_text, title=title)

                parent_id = child_to_parent.get(cid)
                child_id = parent_to_child.get(cid)

                # An active lead is a thread that hasn't been superseded by a revived child
                is_lead = child_id is None

                member = FleetMember(
                    conversation_id=cid,
                    title=title,
                    cluster=cluster_key,
                    cluster_name=CLUSTER_DISPLAY_NAMES.get(cluster_key, cluster_key),
                    message_count=msg_count,
                    remote_update_time=ts or 0.0,
                    updated_at_str=dt_str,
                    parent_conversation_id=parent_id,
                    revived_child_id=child_id,
                    is_active_lead=is_lead,
                    referenced_prs=[f"#{p}" for p in prs],
                    last_user_snippet=u_text[:120].replace("\n", " ").strip(),
                    last_asst_snippet=a_text[:120].replace("\n", " ").strip(),
                )
                clusters[cluster_key].append(member)

        return clusters

    def get_active_leads(self, min_timestamp: float = 1791000000.0) -> dict[str, FleetMember]:
        """Return the highest-priority active lead for each domain cluster."""
        clusters = self.discover_fleet(min_timestamp=min_timestamp)
        leads: dict[str, FleetMember] = {}
        for ckey, members in clusters.items():
            active_leads = [m for m in members if m.is_active_lead]
            if active_leads:
                # Top lead is the one most recently updated
                active_leads.sort(key=lambda m: m.remote_update_time, reverse=True)
                leads[ckey] = active_leads[0]
        return leads


class FleetOrchestrator:
    """Concurrent multi-thread orchestrator for the entire ChatGPT workspace fleet."""

    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        profile_dir: Path | str | None = None,
        browser_mode: str = "displayless",
        session_token: str | None = None,
        model: str | None = None,
    ) -> None:
        self.registry = FleetRegistry(db_path=db_path)
        self.db_path = self.registry.db_path
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.browser_mode = browser_mode
        self.session_token = session_token
        self.model = model

    def status_summary(self) -> dict[str, Any]:
        """Produce a structured status dictionary of all clusters and active leads."""
        clusters = self.registry.discover_fleet()
        leads = self.registry.get_active_leads()
        total_threads = sum(len(mems) for mems in clusters.values())
        return {
            "total_threads": total_threads,
            "clusters": {
                ckey: {
                    "name": CLUSTER_DISPLAY_NAMES.get(ckey, ckey),
                    "thread_count": len(mems),
                    "active_lead": leads[ckey].to_dict() if ckey in leads else None,
                    "all_threads": [m.to_dict() for m in mems],
                }
                for ckey, mems in clusters.items()
            },
        }

    def render_matrix_markdown(self) -> str:
        """Render a clean GitHub Markdown matrix of the fleet status."""
        summary = self.status_summary()
        lines: list[str] = [
            f"# Fleet Coordination Matrix ({summary['total_threads']} active threads)",
            "",
            "| Cluster | Total Threads | Active Lead Thread | Turns | Fatigue Status | Recent Task / Focus |",
            "| :--- | :---: | :--- | :---: | :---: | :--- |",
        ]
        for ckey, cinfo in summary["clusters"].items():
            lead = cinfo.get("active_lead")
            if not lead:
                continue
            cnt = lead["message_count"]
            fatigue = "🚨 Critical (>200)" if cnt > 200 else ("⚠️ Fatigued (>80)" if cnt > 80 else "✅ Fresh")
            title = lead["title"]
            cid = lead["conversation_id"][:8]
            last_snip = (lead.get("last_asst_snippet") or lead.get("last_user_snippet") or "").strip()
            lines.append(
                f"| **{cinfo['name']}** | {cinfo['thread_count']} | `{cid}` (*{title}*) | {cnt} | {fatigue} | {last_snip[:60]}... |"
            )
        lines.append("")
        return "\n".join(lines)

    def auto_revive_fatigued(
        self,
        fatigue_threshold: int = 80,
        continuation_prompts: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Automatically detect and revive all fatigued lead threads across all clusters."""
        leads = self.registry.get_active_leads()
        prompts = continuation_prompts or {}
        revivals: list[dict[str, Any]] = []

        for ckey, lead in leads.items():
            if lead.message_count >= fatigue_threshold:
                default_prompt = (
                    f"Reviving active lead for cluster '{lead.cluster_name}'. "
                    f"Please inspect the attached historical decisions and prepare to continue active tasks."
                )
                prompt = prompts.get(ckey) or prompts.get(lead.conversation_id) or default_prompt
                rev_res = revive_thread(
                    lead.conversation_id,
                    prompt,
                    profile_dir=self.profile_dir,
                    browser_mode=self.browser_mode,
                    db_path=self.db_path,
                    session_token=self.session_token,
                    model=self.model,
                    follow=True,
                )
                revivals.append({
                    "cluster": ckey,
                    "parent_conversation_id": lead.conversation_id,
                    "revived_conversation_id": rev_res.get("revived_conversation_id"),
                    "title": lead.title,
                    "prior_turn_count": lead.message_count,
                    "state": rev_res.get("state"),
                })

        return revivals

    def dispatch_task(
        self,
        cluster_or_cid: str,
        prompt: str,
        *,
        attachments: Sequence[Path | str] = (),
        stream: bool = False,
        output: Path | str | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Dispatch an instruction to a specific conversation or domain cluster's active lead."""
        target_cid = cluster_or_cid
        leads = self.registry.get_active_leads()
        if cluster_or_cid in leads:
            target_cid = leads[cluster_or_cid].conversation_id

        service = MutationService(
            session_token=self.session_token,
            model=self.model,
            db_path=self.db_path,
        )
        return service.continue_thread(
            target_cid,
            prompt,
            attachments=attachments,
            profile_dir=self.profile_dir,
            browser_mode=self.browser_mode,
            write_transport="async",
            dom_fallback=True,
            follow=True,
            progress_callback=progress_callback,
        )

    def dispatch_concurrent(
        self,
        tasks: Mapping[str, str],
        *,
        max_workers: int = 4,
    ) -> dict[str, dict[str, Any]]:
        """Dispatch multiple tasks across domain clusters concurrently.
        
        Requests are safely paced across workers by the underlying GlobalBackendRateLimiter.
        """
        results: dict[str, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_key = {
                executor.submit(self.dispatch_task, ckey, prompt): ckey
                for ckey, prompt in tasks.items()
            }
            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    res = future.result()
                    results[key] = res
                except Exception as exc:
                    results[key] = {"state": "error", "error": str(exc)}

        return results
