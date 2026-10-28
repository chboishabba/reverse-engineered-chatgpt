from re_gpt.gap_comparator import ExactGapComparator, extract_relational_frames


def test_gap_comparator_relational_frames_preserve_boundary_metadata() -> None:
    slots = [
        {
            "slot_id": "wrapper",
            "sentence_id": "s1",
            "head_ordinal": 4,
            "head_lemma": "prove",
            "lemma": "you",
            "is_boundary": True,
            "boundary_kind": "dialogue_deictic",
        },
        {
            "slot_id": "aux",
            "sentence_id": "s1",
            "head_ordinal": 4,
            "head_lemma": "prove",
            "lemma": "to",
            "is_boundary": True,
            "boundary_kind": "auxiliary",
        },
        {
            "slot_id": "actor",
            "sentence_id": "s1",
            "head_ordinal": 4,
            "head_lemma": "prove",
            "lemma": "system",
            "argument_role": "actor",
        },
        {
            "slot_id": "theme",
            "sentence_id": "s1",
            "head_ordinal": 4,
            "head_lemma": "prove",
            "lemma": "consistency",
            "argument_role": "theme",
        },
    ]

    expected = extract_relational_frames(slots)

    assert expected[0]["predicate"] == "prove"
    assert expected[0]["arguments"] == {"actor": ["system"], "theme": ["consistency"]}
    assert [item["boundary_kind"] for item in expected[0]["boundary_metadata"]] == [
        "dialogue_deictic",
        "auxiliary",
    ]
    assert ExactGapComparator.relational_frames(slots) == expected
