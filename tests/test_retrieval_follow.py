from pathlib import Path
import json

import jsonschema

from re_gpt.retrieval_follow import (
    build_conversation_list_follow_artifact,
    build_conversation_list_follow_normalized_artifact,
    write_conversation_list_follow_artifact,
    write_conversation_list_follow_normalized_artifact,
)


ROOT = Path("/home/c/Documents/code/ITIR-suite")
SCHEMA_PATH = ROOT / "schemas" / "itir.normalized.artifact.v1.schema.json"


def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    )


def test_build_conversation_list_follow_artifact_truncates_results():
    artifact = build_conversation_list_follow_artifact(
        query="all conversations",
        result_refs=[f"conv-{i}" for i in range(5)],
        total_results=25,
        max_results=3,
        stop_after=False,
        trigger_params={"page_size": 10},
    )

    envelope = artifact["retrieval_envelope"]
    assert artifact["artifact_type"] == "derived.re_gpt.list_follow"
    assert envelope["scope"]["max_results"] == 3
    assert envelope["scope"]["stop_after"] is False
    assert envelope["result_refs"] == ["conv-0", "conv-1", "conv-2"]
    assert envelope["scope"]["truncated"] is True
    assert artifact["non_authoritative"] is True
    assert artifact["summary"]["retrieved"] == 3


def test_write_conversation_list_follow_artifact_roundtrip(tmp_path: Path):
    out_file = tmp_path / "follow.json"
    payload = write_conversation_list_follow_artifact(
        out_file,
        query="latest",
        result_refs=["conv-A"],
        total_results=1,
        max_results=1,
        stop_after=True,
        trigger_params={},
    )

    assert out_file.exists()
    read_back = out_file.read_text(encoding="utf-8")
    assert '"artifact_type": "derived.re_gpt.list_follow"' in read_back
    assert payload["artifact_id"]


def test_build_conversation_list_follow_normalized_artifact_validates() -> None:
    payload = build_conversation_list_follow_normalized_artifact(
        query="latest",
        result_refs=["conv-A", "conv-B"],
        total_results=2,
        max_results=1,
        stop_after=True,
        trigger_params={"page_size": 10},
    )

    _validator().validate(payload)
    assert payload["artifact_role"] == "derived_product"
    assert payload["authority"]["authority_class"] == "derived_inspection"
    assert payload["authority"]["derived"] is True
    assert payload["unresolved_pressure_status"] == "follow_needed"
    assert payload["summary"]["retrieved"] == 1


def test_write_conversation_list_follow_normalized_artifact_roundtrip(tmp_path: Path) -> None:
    out_file = tmp_path / "follow.normalized.json"
    payload = write_conversation_list_follow_normalized_artifact(
        out_file,
        query="latest",
        result_refs=["conv-A"],
        total_results=1,
        max_results=1,
        stop_after=True,
        trigger_params={},
    )

    assert out_file.exists()
    reloaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert reloaded["artifact_role"] == "derived_product"
    assert reloaded["artifact_id"] == payload["artifact_id"]
