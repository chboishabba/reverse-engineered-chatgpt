"""Regression checks for deterministic semantic extraction."""
from re_gpt.semantic_rejuvenation import extract_factors, candidate_product_lattice, state_capsule

def test_extraction_does_not_invent_proof_status():
    turns = [{"message_index": 7, "content": "Agda proves transition morphism.", "evidence": "source_written"}]
    result = extract_factors(turns)
    assert len(result) == 1
    assert result[0].evidence == "unverified"
    assert "transition morphism" in result[0].object

def test_determinism_and_lattice_candidates():
    turns = [{"message_index": 1, "content": "Rejuvenation requires PostgreSQL.\nRejuvenation extends PNF."}]
    first = extract_factors(turns)
    assert first == extract_factors(turns)
    assert len(first) == 2
    assert candidate_product_lattice(first)["Rejuvenation"] == ("PNF", "PostgreSQL")
    assert state_capsule(first) == state_capsule(extract_factors(turns))

def test_proof_requires_explicit_kernel_evidence():
    turns = [{"message_index": 2, "content": "Agda verifies morphism.", "evidence": "kernel_verified"}]
    assert "Agda verifies morphism" in state_capsule(extract_factors(turns))
