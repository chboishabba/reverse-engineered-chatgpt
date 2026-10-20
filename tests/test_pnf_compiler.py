import collections
import sqlite3
import sys
import types
from pathlib import Path

from re_gpt.pnf_compiler import PnfFleetCompiler


def test_compile_fleet_parses_text_beyond_previous_30k_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    """Late thread decisions must reach the PNF parser, not just the first 30K chars."""
    db_path = tmp_path / "history.sqlite3"
    late_marker = "LATE_PNF_DECISION_MUST_BE_COMPILED"
    full_text = ("early evidence. " * 2_500) + late_marker

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                remote_update_time REAL
            );
            CREATE TABLE messages (
                conversation_id TEXT,
                message_index INTEGER,
                author TEXT,
                content TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?)", ("thread-1", "Thread", 1.0)
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)", ("thread-1", 0, "user", full_text)
        )

    seen_texts: list[str] = []

    class FakeNlp:
        def __call__(self, text: str):
            seen_texts.append(text)
            return types.SimpleNamespace(sents=[])

    monkeypatch.setitem(
        sys.modules,
        "spacy",
        types.SimpleNamespace(load=lambda *_args, **_kwargs: FakeNlp()),
    )

    def fake_run(command, **_kwargs):
        Path(command[command.index("--output") + 1]).write_bytes(b"")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("re_gpt.pnf_compiler.subprocess.run", fake_run)
    compiler = PnfFleetCompiler(
        db_path=db_path,
        compiler_bin=tmp_path / "sensiblaw-world-compiler",
        output_dir=tmp_path / "world",
    )
    monkeypatch.setattr(compiler, "_inspect_slrw_pnf", lambda _path: ({}, collections.Counter()))

    compiler.compile_fleet(limit=1)

    assert seen_texts == [full_text]
    assert late_marker in seen_texts[0]
