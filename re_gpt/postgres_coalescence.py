"""Conservative coalescence proposals from PostgreSQL evidence and supplied manifold.

Discovery does not retire parents or send conversations. A shared topic alone
cannot establish equivalence: overlap and an explicit deliverable must agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Mapping

from .formal_triangulator import triangulate_formal_family
from .postgres_world_rejuvenation import _manifold_entry, build_world_enhanced_capsule

@dataclass(frozen=True)
class Cluster:
    topic_id: str
    deliverable: str
    parents: tuple[str, ...]
    similarity: float

def discover_coalescence_clusters(conn, candidate_queue, similarity_threshold=0.75,
                                  *, manifold_summary=None, max_size=10):
    if not 0 <= similarity_threshold <= 1 or not 2 <= max_size <= 10:
        raise ValueError("invalid threshold or cluster size")
    queue = sorted(set(candidate_queue))
    if not queue:
        return []
    entries = {cid: _manifold_entry(manifold_summary, cid) for cid in queue}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.conversation_ref, f.lemma
              FROM corpus.chat_archive_message AS m
              JOIN pnf.statement_candidate_factor AS f
                ON f.dependency_ref=m.message_ref
             WHERE m.conversation_ref = ANY(%s)
               AND m.branch_membership_ref='active'
               AND f.candidate_only=true
             ORDER BY m.conversation_ref,f.lemma
        """, (["conversation:" + cid for cid in queue],))
        rows = cur.fetchall()
    lemmas = {cid: set() for cid in queue}
    for cref, lemma in rows:
        cid = cref.removeprefix("conversation:")
        if cid in lemmas and lemma:
            lemmas[cid].add(lemma.casefold())
    # Complete-link agglomeration prevents transitive chaining of unrelated
    # threads through a weak bridge. Greedy, deterministic, non-overlapping.
    def identity(cid):
        e = entries[cid]
        goals = tuple(sorted(str(x) for x in e.get("active_goals", ())))
        return str(e["primary_topic_id"]), str(e["sub_eigen_cluster"]), goals
    def overlap(a, b):
        x, y = lemmas[a], lemmas[b]
        return len(x & y) / len(x | y) if x and y else 0.0
    groups = {}
    for cid in queue:
        groups.setdefault(identity(cid), []).append(cid)
    clusters = []
    for (topic, subtopic, goals), members in sorted(groups.items()):
        if not goals:
            continue
        remaining = set(members)
        while len(remaining) >= 2:
            pairs = [(overlap(a,b),a,b) for a,b in combinations(sorted(remaining),2)]
            if not pairs:
                break
            score,a,b = sorted(pairs,key=lambda p:(-p[0],p[1],p[2]))[0]
            if score < similarity_threshold:
                break
            group = [a,b]
            for candidate in sorted(remaining - {a,b}):
                if len(group) == max_size:
                    break
                if all(overlap(candidate, other) >= similarity_threshold for other in group):
                    group.append(candidate)
            clusters.append(Cluster(topic, goals[0], tuple(sorted(group)),
                                    min(overlap(x,y) for x,y in combinations(group,2))))
            remaining.difference_update(group)
    return sorted(clusters,key=lambda c:(-c.similarity,c.topic_id,c.parents))

def build_coalesced_world_capsule(conn, thread_ids, topic_id, *,
                                  manifold_summary, adjacent_limit=50,
                                  formal_repo_root=None, formal_files=None):
    parents = tuple(sorted(set(thread_ids)))
    if not 2 <= len(parents) <= 10:
        raise ValueError("coalescence requires 2–10 distinct parents")
    entries = [_manifold_entry(manifold_summary, cid) for cid in parents]
    if any(str(e["primary_topic_id"]) != str(topic_id) for e in entries):
        raise ValueError("topic mismatch")
    subtopics = {str(e["sub_eigen_cluster"]) for e in entries}
    if len(subtopics) != 1:
        raise ValueError("cannot coalesce distinct sub-eigen clusters")
    goal_sets = [set(map(str, e.get("active_goals", ()))) for e in entries]
    common_goals = set.intersection(*goal_sets) if goal_sets else set()
    if not common_goals:
        raise ValueError("no shared explicit deliverable")
    lines = [f"# Coalesced topic: {topic_id}",
             f"Sub-eigen cluster: {next(iter(subtopics))}",
             "## Shared deliverables"]
    lines.extend(f"- {g}" for g in sorted(common_goals))

    # Optional formal family triangulation across candidate Agda files
    selected_formal = list(formal_files) if formal_files else []
    if not selected_formal and formal_repo_root:
        root = Path(formal_repo_root)
        if root.is_dir():
            terms = [str(topic_id), str(next(iter(subtopics)))] + list(common_goals)
            found = set()
            for t in terms:
                found.update(root.glob(f"**/*{t}*.agda"))
            if len(found) >= 2:
                selected_formal = sorted(list(found))[:10]

    if len(selected_formal) >= 2:
        try:
            triangulation = triangulate_formal_family(
                selected_formal, family_name=f"{topic_id}_{next(iter(subtopics))}"
            )
            lines.append("## Formal Family Triangulation & Consensus Invariants")
            lines.append(triangulation.render_markdown())
        except Exception:
            pass

    for cid in parents:
        lines.append(f"## Parent conversation:{cid} (source attribution retained)")
        lines.append(build_world_enhanced_capsule(
            conn, cid, manifold_summary, adjacent_limit=adjacent_limit,
            formal_repo_root=formal_repo_root))
    lines.append("Candidate overlap is not proof equivalence; no parent is retired by this capsule.")
    return "\n".join(lines) + "\n"
