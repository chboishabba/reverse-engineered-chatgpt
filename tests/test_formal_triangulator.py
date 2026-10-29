from pathlib import Path
import pytest

from re_gpt.formal_triangulator import (
    FormalFamilyTriangulation,
    _extract_status_fields_and_values,
    triangulate_formal_family,
)

MODULE_A = """
module Test.Round12 where

open import Agda.Builtin.Bool using (Bool; true; false)

clayYangMillsPromoted : Bool
clayYangMillsPromoted = false

record Round12Status : Set where
  field
    invariantAlpha : Bool
    divergentBeta : Bool
    universalGapGamma : Bool

canonicalRound12Status : Round12Status
canonicalRound12Status =
  true
  true
  false
"""

MODULE_B = """
module Test.Round13 where

open import Agda.Builtin.Bool using (Bool; true; false)

clayYangMillsPromoted : Bool
clayYangMillsPromoted = false

record Round13Status : Set where
  field
    invariantAlpha : Bool
    divergentBeta : Bool
    universalGapGamma : Bool

canonicalRound13Status : Round13Status
canonicalRound13Status =
  true
  false
  false
"""

MODULE_C = """
module Test.ClaySprintSeventyEightYMReceipt where

open import Agda.Builtin.Bool using (Bool; true; false)
open import Agda.Builtin.Equality using (_≡_; refl)

clayPromoted : Bool
clayPromoted = false

data Sprint78YMInput : Set where
  MixedPlaquettesAreTransferMatrixFaceAction : Sprint78YMInput

data Sprint78YMOpenGate : Set where
  AllDiameterWeightedKP : Sprint78YMOpenGate

record ClaySprintReceipt : Set where
  field
    paper12bB6A1A3RouteRecorded : Bool
    paper12bB6A1A3RouteRecordedIsTrue : paper12bB6A1A3RouteRecorded ≡ true

    allDiameterKPActivityBound : Bool
    allDiameterKPActivityBoundIsFalse : allDiameterKPActivityBound ≡ false
"""

MODULE_D = """
module Test.ClaySprintSeventyEightYMOtherReceipt where

open import Agda.Builtin.Bool using (Bool; true; false)
open import Agda.Builtin.Equality using (_≡_; refl)

clayPromoted : Bool
clayPromoted = false

data Sprint78YMInput : Set where
  MixedPlaquettesAreTransferMatrixFaceAction : Sprint78YMInput

data Sprint78YMOpenGate : Set where
  AllDiameterWeightedKP : Sprint78YMOpenGate

record ClaySprintReceipt : Set where
  field
    paper12bB6A1A3RouteRecorded : Bool
    paper12bB6A1A3RouteRecordedIsTrue : paper12bB6A1A3RouteRecorded ≡ true

    allDiameterKPActivityBound : Bool
    allDiameterKPActivityBoundIsFalse : allDiameterKPActivityBound ≡ false
"""


def test_extract_mock_moonshine():
    extracted = _extract_status_fields_and_values(MODULE_A)
    assert extracted["invariantAlpha"]["value"] is True
    assert extracted["divergentBeta"]["value"] is True
    assert extracted["universalGapGamma"]["value"] is False
    assert extracted["clayYangMillsPromoted"]["value"] is False


def test_extract_mock_clay_sprint():
    extracted = _extract_status_fields_and_values(MODULE_C)
    assert extracted["clayPromoted"]["value"] is False
    assert extracted["MixedPlaquettesAreTransferMatrixFaceAction"]["value"] is True
    assert extracted["AllDiameterWeightedKP"]["value"] is False
    assert extracted["paper12bB6A1A3RouteRecorded"]["value"] is True
    assert extracted["allDiameterKPActivityBound"]["value"] is False


def test_triangulate_moonshine_divergence(tmp_path: Path):
    f1 = tmp_path / "Round12.agda"
    f2 = tmp_path / "Round13.agda"
    f1.write_text(MODULE_A)
    f2.write_text(MODULE_B)

    tri = triangulate_formal_family([f1, f2], family_name="MoonshineRoundFamily")
    assert "invariantAlpha" in tri.shared_invariants
    assert "clayYangMillsPromoted" in tri.unresolved_gap_intersection
    assert "universalGapGamma" in tri.unresolved_gap_intersection
    assert any(dc.field_or_lemma == "divergentBeta" for dc in tri.divergent_constructions)

    md = tri.render_markdown()
    assert "Shared Invariant Baseline" in md
    assert "Divergent Construction Branches" in md
    assert "Unresolved Gap Intersection" in md
    assert "`invariantAlpha`" in md or "invariantAlpha" in md
    assert "universalGapGamma" in md


def test_triangulate_clay_sprint_ym(tmp_path: Path):
    f1 = tmp_path / "Sprint78A.agda"
    f2 = tmp_path / "Sprint78B.agda"
    f1.write_text(MODULE_C)
    f2.write_text(MODULE_D)

    tri = triangulate_formal_family([f1, f2], family_name="Sprint78YM")
    assert "MixedPlaquettesAreTransferMatrixFaceAction" in tri.shared_invariants
    assert "paper12bB6A1A3RouteRecorded" in tri.shared_invariants
    assert "clayPromoted" in tri.unresolved_gap_intersection
    assert "AllDiameterWeightedKP" in tri.unresolved_gap_intersection
    assert "allDiameterKPActivityBound" in tri.unresolved_gap_intersection
