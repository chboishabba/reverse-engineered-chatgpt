"""Exact PNF Multiset Gap Operator (Delta_Gap = M_informal \\ M_formal).

Compares an informal research thread's compiled PNF semantic multiset against
the AST / formal specification multiset of an Agda/Lean4 formalization file.
Implements the zero-residue provenance comparison doctrine from slr.
"""

from __future__ import annotations

import collections
from dataclasses import asdict, dataclass
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence

import spacy

from .pnf_compiler import (
    DEFAULT_WORLD_DIR,
    FRAGMENT_NAMES,
    SHAPE_NAMES,
    SPACY_DEP_TO_SHAPE,
)


@dataclass
class GapReport:
    conversation_id: str
    formal_file: str
    informal_semantic_factors: int
    formal_semantic_factors: int
    matched_semantic_factors: int
    unmatched_informal_factors: int  # Unformalized premises / high-alpha work items
    unmatched_formal_factors: int    # Formal constraints unaddressed in thread
    exact_multiset_match: bool
    top_missing_premises: list[dict[str, Any]]
    top_unaddressed_formal_constraints: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_markdown(self) -> str:
        cid_short = self.conversation_id[:8]
        file_name = Path(self.formal_file).name
        exact_str = "✅ EXACT MATCH (Zero Residual)" if self.exact_multiset_match else "⚠️ RESIDUAL GAP DETECTED"

        lines = [
            f"# PNF Gap Analysis Receipt: `{cid_short}` vs `{file_name}`",
            "",
            f"**Status:** {exact_str}  ",
            f"**Informal Source Thread:** `{self.conversation_id}`  ",
            f"**Formal Specification File:** `{self.formal_file}`  ",
            "",
            "## 1. Multiset Accounting Summary",
            "",
            "| Metric | Count | Architectural Meaning |",
            "| :--- | :---: | :--- |",
            f"| **Informal Semantic Factors** | **{self.informal_semantic_factors}** | Total non-Other factors from research thread |",
            f"| **Formal Semantic Factors** | **{self.formal_semantic_factors}** | Total semantic factors in formal Agda/Lean specification |",
            f"| **Matched Semantic Factors** | **{self.matched_semantic_factors}** | Factors with identical (role, lemma, dependency) |",
            f"| **Unmatched Informal Factors** | **{self.unmatched_informal_factors}** | **Missing formalization premises (Alpha targets)** |",
            f"| **Unmatched Formal Factors** | **{self.unmatched_formal_factors}** | Formal invariants not addressed in research thread |",
            "",
            "## 2. Top Unformalized Premises (Missing in Formal AST)",
            "",
            "These factors represent explicit hypotheses, lemmas, or physical bounds asserted in the thread that have no counterpart in the formal codebase:",
            "",
            "| Rank | Factor Signature `(role, lemma, dep)` | Residual Count in Discussion | Recommended Formalization Target |",
            "| :---: | :--- | :---: | :--- |",
        ]
        for idx, p in enumerate(self.top_missing_premises[:25], 1):
            sig = p["signature"]
            cnt = p["count"]
            role = sig.split(":")[0].capitalize()
            lines.append(f"| {idx} | `{sig}` | {cnt} | Add formal `{role}` premise / lemma constraint |")

        lines.extend([
            "",
            "## 3. Top Formal Invariants Not Addressed in Discussion",
            "",
            "These factors exist in the formal AST but were never referenced or discussed in the research thread:",
            "",
            "| Rank | Factor Signature `(role, lemma, dep)` | Unaddressed Formal Count | Formal Meaning |",
            "| :---: | :--- | :---: | :--- |",
        ])
        for idx, f in enumerate(self.top_unaddressed_formal_constraints[:15], 1):
            sig = f["signature"]
            cnt = f["count"]
            lines.append(f"| {idx} | `{sig}` | {cnt} | Hard typed boundary in formal specification |")

        lines.append("")
        return "\n".join(lines)


class ExactGapComparator:
    """Computes exact multiset difference between compiled informal threads and formal files."""

    def __init__(self, slrw_path: Path | str | None = None) -> None:
        if slrw_path:
            self.slrw_path = Path(slrw_path).expanduser()
        else:
            self.slrw_path = DEFAULT_WORLD_DIR / "fleet_top200_iter1.slrw"
        self._nlp = None

    @property
    def nlp(self):
        if self._nlp is None:
            self._nlp = spacy.load("en_core_web_sm", disable=["ner"])
        return self._nlp

    def load_thread_multiset(self, conversation_id: str) -> collections.Counter:
        """Extract the semantic factor multiset for a conversation from the .slrw world store."""
        multiset: collections.Counter = collections.Counter()
        target_prefix = conversation_id[:8]

        with open(self.slrw_path, "rb") as f:
            while True:
                magic = f.read(4)
                if not magic or magic != b"SLRW":
                    break
                _ver = struct.unpack("<H", f.read(2))[0]
                header = f.read(2)
                kind_u8, _flags = header[0], header[1]
                id_len, aux_len, payload_len = struct.unpack("<III", f.read(12))
                _iteration = struct.unpack("<q", f.read(8))[0]
                rec_id = f.read(id_len).decode("utf-8", errors="replace")
                aux = f.read(aux_len).decode("utf-8", errors="replace") if aux_len else ""
                payload = f.read(payload_len)

                if (target_prefix in aux or target_prefix in rec_id) and kind_u8 == 2 and payload.startswith(b"PNF1"):
                    frag_u8 = payload[4]
                    shape_u8 = payload[5]
                    role_str = FRAGMENT_NAMES.get(frag_u8, "other")
                    dep_str = SHAPE_NAMES.get(shape_u8, "unresolved")

                    pos = 32
                    try:
                        olen = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4 + olen
                        llen = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4
                        lemma = payload[pos : pos + llen].decode("utf-8", errors="replace")
                        sig = f"{role_str}:{lemma.lower()}:{dep_str}"
                        multiset[sig] += 1
                    except Exception:
                        continue
        return multiset

    def extract_formal_multiset(self, formal_path: Path | str) -> collections.Counter:
        """Extract the semantic factor multiset from an Agda or Lean4 source specification."""
        path = Path(formal_path).expanduser()
        text = path.read_text(encoding="utf-8", errors="replace")
        multiset: collections.Counter = collections.Counter()

        doc = self.nlp(text)
        for sid, sent in enumerate(doc.sents):
            if not sent.text.strip():
                continue
            tok_ord = 0
            for local, tok in enumerate(sent):
                orth = tok.text.strip()
                lemma = (tok.lemma_ or tok.text).strip()
                if not orth or not lemma:
                    continue
                if tok.is_punct or not any(c.isalnum() for c in orth):
                    continue
                shape = SPACY_DEP_TO_SHAPE.get(tok.dep_, 16)
                if shape == 16:
                    continue
                # Map to fragment
                frag = None
                if shape in (1, 3):
                    frag = "actor"
                elif shape == 2:
                    frag = "patient"
                elif shape in (4, 5):
                    frag = "property"
                elif shape == 6:
                    frag = "conjunction"
                elif shape == 7:
                    frag = "negation"
                elif shape == 8:
                    frag = "modality"
                elif shape == 10:
                    frag = "temporal"
                elif shape in (11, 12):
                    frag = "content_clause"
                elif shape in (13, 14, 15):
                    frag = "clause_attachment"

                if frag:
                    dep_name = SHAPE_NAMES.get(shape, "UnresolvedDependency")
                    sig = f"{frag}:{lemma.lower()}:{dep_name}"
                    multiset[sig] += 1
        return multiset

    def compare(self, conversation_id: str, formal_path: Path | str) -> GapReport:
        """Execute exact provenance-backed multiset difference."""
        informal_ms = self.load_thread_multiset(conversation_id)
        formal_ms = self.extract_formal_multiset(formal_path)

        all_keys = set(informal_ms.keys()) | set(formal_ms.keys())

        matched_count = 0
        unmatched_informal_count = 0
        unmatched_formal_count = 0

        missing_premises: list[dict[str, Any]] = []
        unaddressed_formal: list[dict[str, Any]] = []

        for sig in all_keys:
            c_inf = informal_ms.get(sig, 0)
            c_for = formal_ms.get(sig, 0)

            matched = min(c_inf, c_for)
            matched_count += matched

            if c_inf > c_for:
                diff = c_inf - c_for
                unmatched_informal_count += diff
                missing_premises.append({"signature": sig, "count": diff})
            elif c_for > c_inf:
                diff = c_for - c_inf
                unmatched_formal_count += diff
                unaddressed_formal.append({"signature": sig, "count": diff})

        missing_premises.sort(key=lambda x: x["count"], reverse=True)
        unaddressed_formal.sort(key=lambda x: x["count"], reverse=True)

        exact_match = (unmatched_informal_count == 0 and unmatched_formal_count == 0)

        return GapReport(
            conversation_id=conversation_id,
            formal_file=str(formal_path),
            informal_semantic_factors=sum(informal_ms.values()),
            formal_semantic_factors=sum(formal_ms.values()),
            matched_semantic_factors=matched_count,
            unmatched_informal_factors=unmatched_informal_count,
            unmatched_formal_factors=unmatched_formal_count,
            exact_multiset_match=exact_match,
            top_missing_premises=missing_premises,
            top_unaddressed_formal_constraints=unaddressed_formal,
        )
