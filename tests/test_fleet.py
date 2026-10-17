from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from re_gpt.fleet import (
    CLUSTER_DISPLAY_NAMES,
    FleetMember,
    FleetOrchestrator,
    FleetRegistry,
    classify_text,
)


def _init_fleet_test_db(db_path: Path) -> None:
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_lineage (
                conversation_id TEXT PRIMARY KEY,
                parent_conversation_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                revival_doc_path TEXT
            );
        """)
        # Insert 3 threads: one YM, one Max Cut (fatigued), one SensibLaw
        cur.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?)",
            [
                ("c-ym-parent", "Assess Round Modules", 1792000000.0),
                ("c-ym-child", "Assess Round Modules", 1792100000.0),
                ("c-maxcut", "Implement Max Cut Changes", 1792050000.0),
                ("c-law", "SensibLaw Agda Test Case", 1792080000.0),
            ],
        )
        cur.execute("INSERT INTO conversation_lineage VALUES (?, ?, ?, ?)", ("c-ym-child", "c-ym-parent", 1792100000.0, "/tmp/revival.md"))

        # Add messages
        cur.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            [
                ("c-ym-child", 0, "user", "Next step: Balaban CMP109", 1792100001.0),
                ("c-ym-child", 1, "assistant", "ACK CMP109", 1792100002.0),
                ("c-maxcut", 0, "user", "Max Cut CFFI partition", 1792050001.0),
                ("c-law", 0, "user", "SensibLaw actionstep", 1792080001.0),
            ],
        )
        conn.commit()


def test_classify_text_priorities() -> None:
    assert classify_text("SensibLaw Agda Test Case", title="SensibLaw Agda Test Case") == "sensiblaw"
    assert classify_text("Max Cut CFFI Partition", title="Max Cut CFFI") == "max_cut"
    assert classify_text("Round Balaban YM", title="Round Extraction") == "yang_mills"
    assert classify_text("Random topic", title="Random") == "research"


def test_fleet_discovery_and_lineage(tmp_path: Path) -> None:
    db_file = tmp_path / "fleet_test.sqlite3"
    _init_fleet_test_db(db_file)

    registry = FleetRegistry(db_path=db_file)
    clusters = registry.discover_fleet(min_timestamp=1791000000.0)

    # c-ym-parent was revived into c-ym-child, so only c-ym-child should be active lead
    leads = registry.get_active_leads(min_timestamp=1791000000.0)
    assert leads["yang_mills"].conversation_id == "c-ym-child"
    assert leads["max_cut"].conversation_id == "c-maxcut"
    assert leads["sensiblaw"].conversation_id == "c-law"


def test_fleet_orchestrator_matrix_markdown(tmp_path: Path) -> None:
    db_file = tmp_path / "fleet_test.sqlite3"
    _init_fleet_test_db(db_file)

    orch = FleetOrchestrator(db_path=db_file)
    matrix = orch.render_matrix_markdown()
    assert "# Fleet Coordination Matrix" in matrix
    assert "DASHI / Physics / Yang-Mills / Agda" in matrix
    assert "Max Cut / Partitioning" in matrix
    assert "SensibLaw / Legal / Governance" in matrix


def test_fleet_dispatch_concurrent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "fleet_test.sqlite3"
    _init_fleet_test_db(db_file)

    orch = FleetOrchestrator(db_path=db_file)

    dispatched = {}

    def fake_dispatch_task(cluster_or_cid: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        dispatched[cluster_or_cid] = prompt
        return {"state": "complete", "assistant_text": f"ACK {cluster_or_cid}"}

    monkeypatch.setattr(orch, "dispatch_task", fake_dispatch_task)

    results = orch.dispatch_concurrent({
        "yang_mills": "Step YM",
        "max_cut": "Step MaxCut",
        "sensiblaw": "Step SensibLaw",
    })

    assert len(results) == 3
    assert dispatched["yang_mills"] == "Step YM"
    assert dispatched["max_cut"] == "Step MaxCut"
    assert dispatched["sensiblaw"] == "Step SensibLaw"
