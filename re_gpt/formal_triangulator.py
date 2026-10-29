"""Formal AST Deduplication and Divergence Triangulator.

Analyzes families of proliferating versioned/round Agda modules (e.g. RoundNNN,
ClaySprintNN, NSSprintNN).
Extracts:
  1. Shared Invariant Baseline: definitions, hypotheses, and status fields proved true across all versions.
  2. Divergent Construction Branches: distinct competing proof attempts for the same intermediate obligation.
  3. Residual Gap Intersection: the exact unproven hypotheses/goals common across all branches that prevent full proof closure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .formal_slicer import _declarations


@dataclass(frozen=True)
class DivergentConstruction:
    field_or_lemma: str
    approaches_by_module: dict[str, str]  # module_name -> boolean/type/definition
    summary: str


@dataclass(frozen=True)
class FormalFamilyTriangulation:
    family_name: str
    analyzed_modules: tuple[str, ...]
    shared_invariants: tuple[str, ...]
    divergent_constructions: tuple[DivergentConstruction, ...]
    unresolved_gap_intersection: tuple[str, ...]
    frontier_deliverables: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_name": self.family_name,
            "analyzed_modules": list(self.analyzed_modules),
            "shared_invariants": list(self.shared_invariants),
            "divergent_constructions": [
                {
                    "field_or_lemma": dc.field_or_lemma,
                    "approaches_by_module": dc.approaches_by_module,
                    "summary": dc.summary,
                }
                for dc in self.divergent_constructions
            ],
            "unresolved_gap_intersection": list(self.unresolved_gap_intersection),
            "frontier_deliverables": list(self.frontier_deliverables),
        }

    def render_markdown(self) -> str:
        lines = [
            f"# Formal Family Triangulation & Deduplication: `{self.family_name}`",
            f"**Analyzed Modules:** {len(self.analyzed_modules)} files ({', '.join(Path(m).stem for m in self.analyzed_modules[:6])}{'...' if len(self.analyzed_modules) > 6 else ''})",
            "",
            "## 1. Shared Invariant Baseline (Consensus Across All Rounds)",
            f"Found **{len(self.shared_invariants)}** properties verified across all variants:",
        ]
        if self.shared_invariants:
            lines.extend(f"- `[OWNED / VERIFIED]` {inv}" for inv in self.shared_invariants[:25])
            if len(self.shared_invariants) > 25:
                lines.append(f"  *(+ {len(self.shared_invariants) - 25} more shared properties)*")
        else:
            lines.append("- No common invariant fields isolated.")

        lines.extend([
            "",
            "## 2. Divergent Construction Branches (Competing Hypotheses)",
            f"Found **{len(self.divergent_constructions)}** points of algorithmic divergence:",
        ])
        for dc in self.divergent_constructions:
            lines.append(f"### Obligation: `{dc.field_or_lemma}`")
            lines.append(f"*{dc.summary}*")
            for mod, approach in sorted(dc.approaches_by_module.items()):
                lines.append(f"- **{mod}**: `{approach}`")
            lines.append("")

        lines.extend([
            "## 3. Unresolved Gap Intersection (The True Scientific Blocker)",
            "These hypotheses remain `false` / `LIVE` across **every single variant** in the family:",
        ])
        if self.unresolved_gap_intersection:
            lines.extend(f"- `[BLOCKED / UNPROVEN]` {gap}" for gap in self.unresolved_gap_intersection)
        else:
            lines.append("- All tracked hypotheses have been discharged by at least one variant.")

        lines.extend([
            "",
            "## 4. Synthesized Deduplicated Target",
            "Rather than creating another Round<N+1>, reconcile the competing approaches into a single module:",
        ])
        lines.extend(f"- Target Deliverable: {deliv}" for deliv in self.frontier_deliverables)
        return "\n".join(lines) + "\n"


def _extract_status_fields_and_values(source: str) -> dict[str, dict[str, Any]]:
    """Extract fields from records, top-level booleans, and data constructors."""
    fields: dict[str, dict[str, Any]] = {}
    lines = source.splitlines()

    # Pass 1: Join continuation lines where record assignment `; foo =` or signature `foo :` is split
    normalized_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            re.search(r"^\s*[;{]\s*[A-Za-z0-9_]+\s*=\s*$", line)
            or re.search(r"^\s*[A-Za-z0-9_]+\s*:\s*$", line)
            or re.search(r"≡\s*$", line)
        ) and i + 1 < len(lines):
            i += 1
            line = line.rstrip() + " " + lines[i].strip()
        normalized_lines.append(line)
        i += 1

    # 1. Positional status definitions (e.g. Moonshine record RoundStatus)
    in_record = False
    record_fields: list[str] = []
    for line in normalized_lines:
        if re.search(r"\brecord\s+[A-Za-z0-9_]*Status\b", line):
            in_record = True
            continue
        if in_record:
            if re.search(r"^\s*(?:canonical|data|_|open|import)\b", line):
                in_record = False
                continue
            m = re.match(r"^\s*([A-Za-z_][\w\x27]*)\s*:\s*(?:Bool|Set|SearchClass)\b", line)
            if m:
                record_fields.append(m.group(1))

    in_canonical = False
    val_idx = 0
    for line in normalized_lines:
        if re.search(r"^canonical[A-Za-z0-9_]*Status\b", line):
            in_canonical = True
            val_idx = 0
            continue
        if in_canonical:
            if re.search(r"^---\b|^\S", line) and not line.strip().startswith("--"):
                if val_idx >= len(record_fields):
                    in_canonical = False
            m_bool = re.match(r"^\s*(true|false)\b\s*(?:--\s*(.*))?", line, re.I)
            if m_bool and val_idx < len(record_fields):
                val_str = m_bool.group(1).lower()
                comment = (m_bool.group(2) or "").strip()
                field_name = record_fields[val_idx]
                fields[field_name] = {
                    "value": val_str == "true",
                    "comment": comment,
                }
                val_idx += 1

    # 2. SearchClass definitions
    for idx, line in enumerate(normalized_lines):
        m_sc = re.search(r"^([A-Za-z0-9_]+Class)\s*:\s*SearchClass", line)
        if m_sc:
            cls_name = m_sc.group(1)
            subsequent = normalized_lines[idx + 1 : idx + 4]
            for sub in subsequent:
                m_val = re.search(r"^\s*(?:[A-Za-z0-9_]+\s*=\s*)?(OWNED|LIVE|DOWNSTREAM|PRUNED|PARALLEL)\b", sub, re.I)
                if m_val:
                    fields[cls_name] = {
                        "value": m_val.group(1).upper() == "OWNED",
                        "comment": f"SearchClass={m_val.group(1).upper()}",
                        "raw_class": m_val.group(1).upper(),
                    }
                    break

    # 3. Top-level booleans (e.g. clayYangMillsPromoted : Bool ... = false)
    bool_vars = set()
    for line in normalized_lines:
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*Bool\b", line)
        if m:
            bool_vars.add(m.group(1))
        m_val = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(true|false)\b", line)
        if m_val and m_val.group(1) in bool_vars:
            name, val = m_val.group(1), m_val.group(2).lower() == "true"
            fields[name] = {"value": val, "comment": f"top-level Bool {name}={val}"}

    # 4. Equality assertions inside records: fooIsTrue : foo ≡ true
    for line in normalized_lines:
        m_eq = re.search(r"^\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*≡\s*(true|false)\b", line)
        if m_eq:
            sig_name, expr, val = m_eq.group(1), m_eq.group(2).strip(), m_eq.group(3).lower() == "true"
            tokens = expr.split()
            prop = tokens[0].split(".")[-1]
            if prop in ("refl", "true", "false") or not prop:
                prop = re.sub(r"(?:IsTrue|IsFalse|StillFalse)$", "", sig_name)
            fields[prop] = {"value": val, "comment": f"equality assertion {sig_name}"}

    # 5. Direct record instantiation lines: ; foo = true/false
    for line in normalized_lines:
        m_rec = re.search(r"^\s*[;{]\s*([A-Za-z0-9_]+)\s*=\s*(true|false)\b", line)
        if m_rec:
            name, val = m_rec.group(1), m_rec.group(2).lower() == "true"
            fields[name] = {"value": val, "comment": f"record instance {name}={val}"}

    # 6. Data constructors (Open Gates vs Input Invariants)
    in_data = False
    data_kind = None
    for line in normalized_lines:
        m_data = re.search(r"^\s*data\s+([A-Za-z0-9_]+)\s*:\s*Set\b", line)
        if m_data:
            name = m_data.group(1)
            in_data = True
            if any(term in name for term in ("OpenGate", "DownstreamGate", "GapItem", "Unresolved")):
                data_kind = "OPEN_GATE"
            elif any(term in name for term in ("Input", "Verified", "Invariant")):
                data_kind = "INPUT"
            else:
                data_kind = None
            continue

        if in_data:
            if re.search(r"^\S", line) and not line.strip().startswith("--"):
                in_data = False
                data_kind = None
                continue
            if data_kind:
                m_c = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*", line)
                if m_c and not m_c.group(1).startswith("canonical"):
                    c_name = m_c.group(1)
                    if data_kind == "OPEN_GATE":
                        fields[c_name] = {"value": False, "comment": "Open gate constructor in data definition"}
                    elif data_kind == "INPUT":
                        fields[c_name] = {"value": True, "comment": "Input invariant constructor in data definition"}

    return fields


def triangulate_formal_family(
    file_paths: Sequence[str | Path],
    *,
    family_name: str | None = None,
) -> FormalFamilyTriangulation:
    """Triangulate common invariants, divergent approaches, and residual gaps across an Agda family."""
    resolved_files = [Path(p).resolve() for p in file_paths if Path(p).is_file()]
    if not resolved_files:
        raise ValueError("no valid files provided to triangulate")

    fam_name = family_name or Path(resolved_files[0]).stem.replace("Round", "Family_")

    all_fields_by_module: dict[str, dict[str, dict[str, Any]]] = {}
    for f in resolved_files:
        mod_name = f.stem
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            status_map = _extract_status_fields_and_values(content)
            all_fields_by_module[mod_name] = status_map
        except Exception:
            continue

    if not all_fields_by_module:
        raise ValueError("could not extract status records from provided files")

    # Group all encountered field names
    all_field_names = sorted(set(k for m in all_fields_by_module.values() for k in m.keys()))

    shared_invariants: list[str] = []
    unresolved_gap_intersection: list[str] = []
    divergent: list[DivergentConstruction] = []

    for field in all_field_names:
        present_modules = {m: all_fields_by_module[m][field] for m in all_fields_by_module if field in all_fields_by_module[m]}
        values = {m: d["value"] for m, d in present_modules.items()}

        # Check if universally true
        if len(present_modules) >= 2 and all(v is True for v in values.values()):
            shared_invariants.append(field)
        # Check if universally false (unresolved gap)
        elif len(present_modules) >= 2 and all(v is False for v in values.values()):
            unresolved_gap_intersection.append(field)
        # Divergent construction
        elif len(set(values.values())) > 1:
            approaches = {
                m: f"{'true' if v else 'false'}" + (f" ({present_modules[m]['comment']})" if present_modules[m].get("comment") else "")
                for m, v in values.items()
            }
            divergent.append(DivergentConstruction(
                field_or_lemma=field,
                approaches_by_module=approaches,
                summary=f"Divergence detected: {sum(values.values())}/{len(values)} modules assert property true.",
            ))

    frontier_deliverables = [
        f"Discharge universal blocker: {gap}" for gap in unresolved_gap_intersection
    ] + [
        f"Reconcile divergent construction: {dc.field_or_lemma}" for dc in divergent[:5]
    ]

    return FormalFamilyTriangulation(
        family_name=fam_name,
        analyzed_modules=tuple(str(f) for f in resolved_files),
        shared_invariants=tuple(shared_invariants),
        divergent_constructions=tuple(divergent),
        unresolved_gap_intersection=tuple(unresolved_gap_intersection),
        frontier_deliverables=tuple(frontier_deliverables),
    )
