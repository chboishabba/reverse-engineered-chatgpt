from __future__ import annotations

from pathlib import Path

from re_gpt.attachments import LocalAttachment, match_expected_attachments


def _local(tmp_path: Path, subdir: str, name: str, data: bytes, mime: str | None = None) -> LocalAttachment:
    path = tmp_path / subdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    item = LocalAttachment.from_path(path)
    if mime is None:
        return item
    return LocalAttachment(item.path, item.filename, mime, item.size, item.sha256)


def _chat(items: list[dict]) -> dict:
    return {
        "mapping": {
            "user": {
                "parent": "root",
                "children": [],
                "message": {"author": {"role": "user"}, "metadata": {"attachments": items}},
            }
        }
    }


def test_duplicate_basenames_require_distinct_remote_ids(tmp_path) -> None:
    expected = [
        _local(tmp_path, "a", "same.txt", b"alpha", "text/plain"),
        _local(tmp_path, "b", "same.txt", b"bravo!", "text/plain"),
    ]
    chat = _chat([
        {"id": "file-a", "name": "same.txt", "mime_type": "text/plain", "size": 5},
        {"id": "file-b", "name": "same.txt", "mime_type": "text/plain", "size": 6},
    ])
    ok, matched = match_expected_attachments(chat, "user", expected)
    assert ok is True
    assert [item.file_id for item in matched] == ["file-a", "file-b"]


def test_duplicate_basenames_cannot_reuse_one_remote_attachment(tmp_path) -> None:
    expected = [
        _local(tmp_path, "a", "same.txt", b"alpha", "text/plain"),
        _local(tmp_path, "b", "same.txt", b"alpha", "text/plain"),
    ]
    chat = _chat([{"id": "file-a", "name": "same.txt", "mime_type": "text/plain", "size": 5}])
    ok, matched = match_expected_attachments(chat, "user", expected)
    assert ok is False
    assert len(matched) == 1


def test_size_mismatch_rejects_candidate_when_remote_size_is_known(tmp_path) -> None:
    expected = [_local(tmp_path, "a", "large.bin", b"x" * 1024, "application/octet-stream")]
    chat = _chat([{"id": "file-a", "name": "large.bin", "mime_type": "application/octet-stream", "size": 99}])
    ok, matched = match_expected_attachments(chat, "user", expected)
    assert ok is False
    assert matched == []


def test_binary_mime_type_is_matched(tmp_path) -> None:
    expected = [_local(tmp_path, "a", "pixel.png", b"\x89PNG\r\n\x1a\n", "image/png")]
    chat = _chat([{"id": "image-a", "name": "pixel.png", "mime_type": "image/png", "size": 8}])
    ok, matched = match_expected_attachments(chat, "user", expected)
    assert ok is True
    assert matched[0].file_id == "image-a"
