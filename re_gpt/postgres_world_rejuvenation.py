"""PostgreSQL-only topic-centric queue and evidence-bounded world capsule.

Coordinates must come from the supplied manifold summary, never be invented.
No SQLite, SLRW or generic TOE prompt fallback is used.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from .semantic_rejuvenation import extract_factors, state_capsule

def load_postgres_processing_queue(conn, prefix=None, limit=None):
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("limit must be a positive integer")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT substring(conversation_ref from 14) AS conversation_id
              FROM corpus.chat_archive_message
             WHERE branch_membership_ref = 'active'
               AND candidate_only = true
               AND (%s::text IS NULL OR conversation_ref LIKE ('conversation:' || %s || '%%'))
             ORDER BY conversation_id
             LIMIT %s
        """, (prefix, prefix, limit))
        return [row[0] for row in cur.fetchall()]

def _manifold_entry(summary, conversation_id):
    if not isinstance(summary, Mapping):
        raise ValueError("manifold summary is required; cannot infer topic coordinates")
    entries = summary.get("conversations", summary)
    if isinstance(entries, Mapping):
        entry = entries.get(conversation_id)
    elif isinstance(entries, list):
        entry = next((item for item in entries
                      if item.get("conversation_id") == conversation_id), None)
    else:
        entry = None
    if not isinstance(entry, Mapping):
        raise ValueError(f"conversation {conversation_id} missing from manifold")
    required = ("primary_topic_id", "sub_eigen_cluster", "lambda_1", "lambda_2", "lambda_3")
    absent = [k for k in required if entry.get(k) is None]
    if absent:
        raise ValueError("missing manifold coordinates: " + ", ".join(absent))
    return entry

def build_world_enhanced_capsule(conn, conversation_id, manifold_summary=None, *,
                                 adjacent_limit=50, formal_repo_root=None):
    if not 1 <= adjacent_limit <= 500:
        raise ValueError("adjacent_limit outside safe range")
    entry = _manifold_entry(manifold_summary, conversation_id)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT node_ref,role_ref,body_storage_ref,message_time_ref
              FROM corpus.chat_archive_message
             WHERE conversation_ref=%s AND branch_membership_ref='active'
             ORDER BY message_time_ref,node_ref
        """, ("conversation:" + conversation_id,))
        messages = cur.fetchall()
        if not messages:
            raise ValueError("thread has no PostgreSQL messages")
        turns = [dict(message_index=i, node_id=node, author=role, content=body or "",
                      message_time_ref=when)
                 for i, (node, role, body, when) in enumerate(messages)]
        factors = extract_factors(turns)
        lemmas = sorted({part.casefold() for f in factors
                         for part in (f.subject, f.predicate, f.object) if part.strip()})
        if lemmas:
            cur.execute("""
                SELECT candidate_ref,statement_ref,role_ref,lemma,dependency_ref
                  FROM pnf.statement_candidate_factor
                 WHERE lemma = ANY(%s)
                   AND candidate_only=true
                   AND dependency_ref NOT LIKE %s
                 ORDER BY candidate_ref
                 LIMIT %s
            """, (lemmas, f"message:{conversation_id}:%", adjacent_limit))
            adjacent = cur.fetchall()
        else:
            adjacent = []
    lines = [state_capsule(factors).rstrip(),
             "## Topic coordinates (supplied manifold, not inferred)",
             f"- Primary topic: {entry['primary_topic_id']}",
             f"- Sub-eigen cluster: {entry['sub_eigen_cluster']}",
             f"- Eigenvalues: ({entry['lambda_1']}, {entry['lambda_2']}, {entry['lambda_3']})",
             "## Adjacent PostgreSQL candidates (not verified invariants)"]
    lines.extend(f"- [{ref}] {lemma} ({role}); statement={stmt}; dependency={dep}"
                 for ref, stmt, role, lemma, dep in adjacent)
    lines.append("## Topic deliverables")
    goals = entry.get("active_goals", ())
    lines.extend(f"- {goal}" for goal in goals)
    if not goals:
        lines.append("- No verified topic goals supplied; do not invent a generic task.")
    if formal_repo_root is not None:
        from .automatic_formal_evidence import automatic_formal_evidence
        lines.append(automatic_formal_evidence(conn, conversation_id, entry, repo_root=formal_repo_root))
    return "\n".join(lines) + "\n"

def allocate_topic_workers(topics, capacity=20):
    """Golden sublinear width law; eligibility and noTypedMeet are caller gates."""
    if not isinstance(capacity, int) or capacity < 0:
        raise ValueError("invalid capacity")
    remaining = capacity
    result = {}
    for topic in sorted(topics, key=lambda t: (-t["alpha"], str(t["topic_id"]))):
        total = int(topic["total_goals"])
        unblocked = int(topic["unblocked_goals"])
        if total <= 0 or not 0 <= unblocked <= total:
            raise ValueError("invalid goal counts")
        shape = topic["shape"]
        if shape == "sequential":
            requested = 1
        elif shape == "frontier_blocked":
            requested = int(topic["blocker_count"])
        elif shape == "parallel":
            requested = int(topic["sub_eigen_dimension"])
        else:
            raise ValueError("unknown topic shape")
        if requested < 0:
            raise ValueError("negative requested width")
        width = min(remaining, requested)
        result[str(topic["topic_id"])] = dict(
            workers=width,
            score=float(topic["alpha"]) * (unblocked / total) * width,
            eligible=bool(topic.get("pareto_eligible", False)))
        # Never schedule ineligible topics.
        if not result[str(topic["topic_id"])]["eligible"]:
            result[str(topic["topic_id"])]["workers"] = 0
            result[str(topic["topic_id"])]["score"] = 0.0
        else:
            remaining -= width
    return result
