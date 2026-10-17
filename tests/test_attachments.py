from pathlib import Path

from re_gpt.attachments import LocalAttachment, canonical_attachments_for_user_node, match_expected_attachments
from re_gpt.operation_store import MutationOperationStore


def _chat_with_attachment(filename="spec.md", file_id="file_123", mime="text/markdown", size=12):
    return {
        "mapping": {
            "u1": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["use attachment"]},
                    "metadata": {
                        "attachments": [
                            {
                                "name": filename,
                                "mime_type": mime,
                                "size": size,
                                "id": file_id,
                            }
                        ]
                    },
                }
            }
        }
    }


def test_canonical_attachment_extracts_graph_metadata():
    attachments = canonical_attachments_for_user_node(_chat_with_attachment(), "u1")
    assert len(attachments) == 1
    assert attachments[0].filename == "spec.md"
    assert attachments[0].file_id == "file_123"
    assert attachments[0].mime_type == "text/markdown"
    assert attachments[0].size == 12


def test_match_expected_attachment_requires_filename_and_pointer(tmp_path: Path):
    path = tmp_path / "spec.md"
    path.write_text("hello world!", encoding="utf-8")
    expected = LocalAttachment.from_path(path)
    ok, matched = match_expected_attachments(_chat_with_attachment(size=12), "u1", [expected])
    assert ok
    assert matched[0].file_id == "file_123"


def test_match_expected_attachments_does_not_reuse_one_remote_file_for_duplicate_basenames(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "spec.md"
    second = second_dir / "spec.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    ok, matched = match_expected_attachments(
        _chat_with_attachment(filename="spec.md", file_id="file_once", mime="text/markdown", size=3),
        "u1",
        [LocalAttachment.from_path(first), LocalAttachment.from_path(second)],
    )

    assert ok is False
    assert len(matched) == 1


def test_operation_store_persists_local_and_canonical_attachment_provenance(tmp_path: Path):
    path = tmp_path / "spec.md"
    path.write_text("hello world!", encoding="utf-8")
    local = LocalAttachment.from_path(path)
    db = tmp_path / "history.sqlite3"
    with MutationOperationStore(db) as store:
        op = store.create(operation="continue", conversation_id="abc", prompt="hello")
        store.record_local_attachments(op["operation_id"], [local])
        row = store.get(op["operation_id"])["attachments"][0]
        assert row["filename"] == "spec.md"
        assert row["source_path"] == str(path.resolve())
        assert row["sha256"] == local.sha256
        assert row["canonical_file_id"] is None
