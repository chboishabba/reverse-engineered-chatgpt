from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from re_gpt import entrypoint
from re_gpt.revive import build_revival_document, record_lineage, revive_thread


def _init_test_db(db_path: Path, cid: str = "conv-123", title: str = "Assess Round Modules") -> None:
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                remote_update_time REAL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                conversation_id TEXT,
                message_index INTEGER,
                author TEXT,
                content TEXT,
                create_time REAL,
                PRIMARY KEY (conversation_id, message_index)
            );
        """)
        cur.execute("INSERT INTO conversations VALUES (?, ?, ?)", (cid, title, 1729000000.0))
        cur.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            [
                (cid, 0, "user", "Status from local CI: PR #1164 and PR #1099 are ready.", 1729000010.0),
                (cid, 1, "assistant", "Confirmed. Merge PR #1164 first, then re-scan signatures.", 1729000020.0),
            ],
        )
        conn.commit()


def test_build_revival_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_history.sqlite3"
    _init_test_db(db_file, "conv-123", "Assess Round Modules")

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("re_gpt.revive.REVIVAL_DIR", cache_dir)

    doc_path, title = build_revival_document("conv-123", db_path=db_file)
    assert doc_path.exists()
    assert title == "Assess Round Modules"
    content = doc_path.read_text(encoding="utf-8")
    assert "# Conversation Context Revival: Assess Round Modules" in content
    assert "`conv-123`" in content
    assert "#1099" in content and "#1164" in content
    assert "Turn 0 — USER" in content
    assert "Turn 1 — ASSISTANT" in content


def test_record_lineage(tmp_path: Path) -> None:
    db_file = tmp_path / "test_history.sqlite3"
    _init_test_db(db_file, "conv-123")

    record_lineage(db_file, "conv-new-456", "conv-123", "/path/to/doc.md")

    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT parent_conversation_id, revival_doc_path FROM conversation_lineage WHERE conversation_id = ?", ("conv-new-456",))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "conv-123"
        assert row[1] == "/path/to/doc.md"


def test_revive_thread_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_history.sqlite3"
    _init_test_db(db_file, "conv-parent")

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("re_gpt.revive.REVIVAL_DIR", cache_dir)

    calls: list[tuple[str, Any]] = []

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            self._db_path = db_file

        def new_conversation(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(("new_conversation", (prompt, kwargs)))
            return {"resolved_thread_id": "conv-child-789", "state": "complete"}

        def continue_thread(self, conversation_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(("continue_thread", (conversation_id, prompt, kwargs)))
            return {
                "operation_id": "op-revive",
                "conversation_id": conversation_id,
                "state": "complete",
                "assistant_text": "Understood. Proceeding with revived task.",
            }

    monkeypatch.setattr("re_gpt.revive.MutationService", FakeService)

    extra_attach = tmp_path / "extra.txt"
    extra_attach.write_text("extra payload", encoding="utf-8")

    res = revive_thread(
        "conv-parent",
        "Next step: re-scan YangMills signatures.",
        attachments=[extra_attach],
        db_path=db_file,
    )

    assert res["state"] == "complete"
    assert res["parent_conversation_id"] == "conv-parent"
    assert res["revived_conversation_id"] == "conv-child-789"
    assert "Understood" in res["assistant_text"]

    assert len(calls) == 2
    assert calls[0][0] == "new_conversation"
    assert calls[1][0] == "continue_thread"
    assert calls[1][1][0] == "conv-child-789"
    prompt_text = calls[1][1][1]
    assert "We are reviving work from previous conversation `conv-parent`" in prompt_text
    assert "Next step: re-scan YangMills signatures." in prompt_text

    passed_attachments = calls[1][1][2]["attachments"]
    assert len(passed_attachments) == 2
    assert passed_attachments[0].name == "conv-parent_revival.md"
    assert passed_attachments[1].name == "extra.txt"


def test_write_main_revive_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict[str, Any] = {}

    def fake_revive_thread(cid: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["cid"] = cid
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {
            "operation_id": "op-cli-revive",
            "conversation_id": "conv-revived-999",
            "state": "complete",
            "assistant_text": "Revival successful.",
        }

    monkeypatch.setattr(entrypoint, "revive_thread", fake_revive_thread)

    code = entrypoint.write_main(["--revive", "conv-old-111", "--prompt", "Proceed with plan"])
    assert code == 0
    assert seen["cid"] == "conv-old-111"
    assert seen["prompt"] == "Proceed with plan"
    out = capsys.readouterr().out
    assert "Revival successful." in out
