"""Bounded automatic formal evidence for PostgreSQL rejuvenation capsules.

No binary world store. A search hit, gap match, or preflight diagnostic is
not a kernel verification receipt.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
import re
import subprocess

def _run(argv, cwd, timeout):
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, check=False)
        return {"status": "ok" if p.returncode == 0 else "failed",
                "exit_code": p.returncode,
                "output": (p.stdout + "\n" + p.stderr)[:6000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "output": type(exc).__name__ + ": " + str(exc)[:300]}

def _formal_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix != ".agda" or not path.is_file():
        return None
    return path

def _multiset_gap(informal, formal):
    # Exact *surface-token* multiset comparison only. This is not a
    # substitute for ExactGapComparator's semantic (role,lemma,dep) operator.
    return {"unformalized_premises": list((informal-formal).elements())[:30],
            "unaddressed_formal_constraints": list((formal-informal).elements())[:30]}

def automatic_formal_evidence(conn, conversation_id, entry, *, repo_root,
                              timeout=4, max_symbols=12):
    root = Path(repo_root).resolve()
    if timeout <= 0 or not 1 <= max_symbols <= 100:
        raise ValueError("invalid formal bounds")
    files = entry.get("formal_files", ())
    if isinstance(files, str):
        files = [files]
    if not files:
        return "## Automatic formal evidence\n- No topic-linked formal files supplied; no formal claim.\n"
    with conn.cursor() as cur:
        cur.execute("""
            SELECT f.role_ref,f.lemma,f.dependency_ref
              FROM pnf.statement_candidate_factor AS f
              JOIN corpus.chat_archive_message AS m
                ON m.message_ref=f.dependency_ref
             WHERE m.conversation_ref=%s AND f.candidate_only=true
             ORDER BY f.candidate_ref LIMIT 500
        """, ("conversation:" + conversation_id,))
        rows = cur.fetchall()
    informal = Counter(str(lemma).casefold() for _, lemma, _ in rows if lemma)
    lines = ["## Automatic formal evidence (candidate-only; not kernel verification)"]
    for rel in sorted(set(files)):
        path = _formal_path(root, rel)
        if path is None:
            lines.append(f"- Formal target unavailable/invalid: {rel!r}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        formal = Counter(x.casefold() for x in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text))
        gap = _multiset_gap(informal, formal)
        lines.extend([f"### {path.relative_to(root)}",
                      "- Surface-token gap diagnostic (NOT ExactGapComparator semantic receipt):",
                      "- Unformalized premises: " + json.dumps(gap["unformalized_premises"]),
                      "- Unaddressed formal tokens: " + json.dumps(gap["unaddressed_formal_constraints"])])
        terms = sorted({str(lemma) for _, lemma, _ in rows if lemma and len(str(lemma)) >= 3})[:max_symbols]
        if terms:
            # rg -F, fixed string, never shell; only searched under repo root.
            matches = []
            for term in terms:
                res = _run(["rg", "-n", "-F", "--max-count", "3", "--glob", "*.agda",
                            "--", term, "."], root, timeout)
                if res["status"] == "ok":
                    matches.extend(res["output"].splitlines()[:3])
                elif res["status"] == "unavailable":
                    lines.append("- Ripgrep unavailable: " + res["output"])
                    break
            lines.append("- Existing source matches (inspect AST/preflight before reuse):")
            lines.extend("  - " + x[:300] for x in matches[:max_symbols])
        preflight = _run(["dashi-agda-preflight", "--json", str(path)], root, timeout)
        lines.append("- Preflight: " + json.dumps(preflight, ensure_ascii=False))
        lines.append("- AST / itir.formal.certify / itir.ao.gap_check: not invoked; no executable adapter verified.")
        lines.append("- kernel_verified PostgreSQL receipts: unavailable (no verified schema field or attestation join supplied).")
    symbols = entry.get("formal_symbols", ())
    if symbols:
        from .formal_slicer import build_pruned_formal_slice, related_symbol_candidates
        slice_result = build_pruned_formal_slice(root, symbols, source_files=files)
        lines.append("## Bounded formal slice (not standalone or kernel verified)")
        lines.append("```agda\n" + slice_result.text + "\n```")
        for symbol in symbols:
            lines.append("- Symbol candidates for " + str(symbol) + ": " +
                         json.dumps(related_symbol_candidates(root, str(symbol), source_files=files)))
    return "\n".join(lines) + "\n"
