"""Deterministic, database-independent semantic extraction for PostgreSQL rejuvenation.

No persistence fallback is provided. Callers must register candidates in PostgreSQL
and validate evidence before promoting proof claims.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping

CLAIM = re.compile(r"^\s*(?P<subject>[A-Za-z][\w .:/#-]{1,100}?)\s+(?P<verb>implements|proves|depends on|extends|blocks|requires|imports|verifies|refutes)\s+(?P<object>[^\n.!?]{2,250})[.!?]?\s*$", re.I)
PROOF_WORDS = {"proves", "verifies"}
EVIDENCE = {"kernel_verified", "source_written", "executed_check", "inherited", "unverified"}

@dataclass(frozen=True)
class Factor:
    subject: str
    predicate: str
    object: str
    source_turn: int
    evidence: str = "unverified"

    def __post_init__(self):
        if self.evidence not in EVIDENCE:
            raise ValueError("unknown evidence class")

    @property
    def digest(self) -> str:
        return sha256(json.dumps([self.subject, self.predicate, self.object, self.source_turn, self.evidence], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def extract_factors(turns: Iterable[Mapping[str, object]]) -> list[Factor]:
    """Conservative extraction: never treat arbitrary prose as an established fact."""
    factors: dict[str, Factor] = {}
    for i, turn in enumerate(turns):
        text = str(turn.get("content") or "")
        turn_id = int(turn.get("message_index", i))
        evidence = str(turn.get("evidence") or "unverified")
        for line in text.splitlines():
            match = CLAIM.fullmatch(line.strip())
            if not match:
                continue
            verb = match.group("verb").lower()
            # Proof claims remain unverified unless an independently checked
            # provenance record explicitly marks them kernel_verified.
            classification = evidence if evidence in EVIDENCE else "unverified"
            if verb in PROOF_WORDS and classification != "kernel_verified":
                classification = "unverified"
            factor = Factor(match.group("subject").strip(), verb, match.group("object").strip(), turn_id, classification)
            factors[factor.digest] = factor
    return sorted(factors.values(), key=lambda f: (f.source_turn, f.digest))

def candidate_product_lattice(factors: Iterable[Factor]) -> dict[str, tuple[str, ...]]:
    """Group candidate objects by subject; this is not a proved mathematical lattice."""
    groups: dict[str, set[str]] = {}
    for factor in factors:
        groups.setdefault(factor.subject, set()).add(factor.object)
    return {k: tuple(sorted(v)) for k, v in sorted(groups.items())}

def state_capsule(factors: Iterable[Factor], adjacent: Iterable[Factor] = ()) -> str:
    """Render a bounded, deterministic world-relative capsule with provenance."""
    primary = sorted(set(factors), key=lambda f: (f.source_turn, f.digest))
    related = sorted(set(adjacent) - set(primary), key=lambda f: (f.subject, f.digest))
    sections = ["# Rejuvenation state", "## Thread statements"]
    sections.extend(f"- [{f.evidence}] {f.subject} {f.predicate} {f.object} (turn {f.source_turn})" for f in primary)
    sections.append("## Verified theorems")
    sections.extend(f"- {f.subject} {f.predicate} {f.object}" for f in primary if f.evidence == "kernel_verified" and f.predicate in PROOF_WORDS)
    sections.append("## Blockers")
    sections.extend(f"- {f.subject} {f.predicate} {f.object}" for f in primary if f.predicate in {"blocks", "requires"})
    sections.append("## Adjacent world statements")
    sections.extend(f"- [{f.evidence}] {f.subject} {f.predicate} {f.object}" for f in related)
    return "\n".join(sections) + "\n"
