"""Conservative bounded Agda declaration slicing.

Only emits compilable *candidate* slices when closure can be proven local by
the installed AST adapter. Without an inspected adapter, emits a source
excerpt with unresolved-dependency diagnostics, never a false standalone proof.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re

@dataclass(frozen=True)
class FormalSlice:
    source: str
    declarations: tuple[str, ...]
    unresolved: tuple[str, ...]
    standalone: bool
    text: str

def _declarations(source):
    lines = source.splitlines()
    starts = []
    pattern = re.compile(r"^([A-Za-z_][\w']*)\s*(?::|=|\bwhere\b)")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            starts.append((i,m.group(1)))
    out = {}
    for n,(i,name) in enumerate(starts):
        end = starts[n+1][0] if n+1 < len(starts) else len(lines)
        out.setdefault(name,(i+1,"\n".join(lines[i:end]).rstrip()))
    return out

def build_pruned_formal_slice(repo_root, target_symbols, max_depth=2,
                              max_declarations=20, *, source_files=None):
    if not 0 <= max_depth <= 8 or not 1 <= max_declarations <= 100:
        raise ValueError("invalid slice bounds")
    root = Path(repo_root).resolve()
    source_files = tuple(source_files or ())
    targets = tuple(dict.fromkeys(target_symbols))
    indexed = {}
    for rel in sorted(set(source_files)):
        p = (root / rel).resolve()
        if not p.is_relative_to(root) or p.suffix != ".agda" or not p.is_file():
            continue
        for name,(line,body) in _declarations(p.read_text(encoding="utf-8")).items():
            indexed.setdefault(name,(str(p.relative_to(root)),line,body))
    selected = {}
    pending = [(x.split(".")[-1],0) for x in targets]
    unresolved = set()
    while pending and len(selected) < max_declarations:
        name,depth = pending.pop(0)
        if name in selected:
            continue
        if name not in indexed:
            unresolved.add(name)
            continue
        rel,line,body = indexed[name]
        selected[name]=(rel,line,body)
        if depth < max_depth:
            for other in sorted(indexed):
                if other != name and re.search(r"(?<![\w'])"+re.escape(other)+r"(?![\w'])",body):
                    if other not in selected:
                        pending.append((other,depth+1))
    if pending:
        unresolved.add("declaration limit reached")
    # Textual top-level boundaries cannot prove Agda AST closure, imports,
    # fixities, constructors or scope; never label this standalone.
    header = ["-- Candidate Agda source excerpt; NOT a standalone kernel-checked module.",
              "-- No AST adapter verified; dependency closure is lexical only."]
    for name,(rel,line,body) in selected.items():
        header.extend([f"-- {rel}:{line} {name}",body,""])
    if unresolved:
        header.append("-- Unresolved: "+", ".join(sorted(unresolved)))
    return FormalSlice(source="lexical-fallback",declarations=tuple(selected),
                       unresolved=tuple(sorted(unresolved)),standalone=False,
                       text="\n".join(header))

def related_symbol_candidates(repo_root, symbol, *, source_files=()):
    """Exact local declaration lookup; never invent similarity categories."""
    result=build_pruned_formal_slice(repo_root,[symbol],max_depth=0,
                                     source_files=source_files)
    return [{"symbol":x,"category":"exact_name_candidate","verified":False}
            for x in result.declarations if x==symbol.split(".")[-1]]
