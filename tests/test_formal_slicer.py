"""Conservative bounded Agda slicing tests."""
from re_gpt.formal_slicer import build_pruned_formal_slice, related_symbol_candidates

def test_dependency_closure_and_unrelated_exclusion(tmp_path):
    p=tmp_path/"Example.agda"
    p.write_text("helper : Set\nhelper = Set\n"
                 "target : Set\ntarget = helper\n"
                 "unrelated : Set\nunrelated = Set\n")
    result=build_pruned_formal_slice(tmp_path,["target"],
                                    source_files=["Example.agda"])
    assert "target" in result.declarations
    assert "helper" in result.declarations
    assert "unrelated" not in result.declarations
    assert not result.standalone
    assert "Example.agda:" in result.text

def test_missing_symbol_fails_without_fabricated_proof(tmp_path):
    result=build_pruned_formal_slice(tmp_path,["absent"],source_files=[])
    assert "absent" in result.unresolved
    assert result.declarations==()
    assert "NOT a standalone" in result.text

def test_exact_name_candidate_is_not_signature_equivalence(tmp_path):
    (tmp_path/"Example.agda").write_text("target : Set\ntarget = Set\n")
    result=related_symbol_candidates(tmp_path,"target",source_files=["Example.agda"])
    assert result==[{"symbol":"target","category":"exact_name_candidate","verified":False}]
