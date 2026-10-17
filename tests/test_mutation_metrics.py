from __future__ import annotations

from re_gpt.mutation_metrics import summarize_values, timing_report
from re_gpt.operation_store import MutationOperationStore


def test_summarize_values_reports_percentiles() -> None:
    result = summarize_values([1.0, 2.0, 3.0, 4.0])
    assert result["n"] == 4
    assert result["p50_s"] == 2.5
    assert result["p95_s"] is not None
    assert result["p50_ci95_s"] is not None


def test_timing_report_reads_operation_rows(tmp_path) -> None:
    db = tmp_path / "history.sqlite3"
    with MutationOperationStore(db) as store:
        op = store.create(operation="continue", conversation_id="abc", prompt="hello")
        store.update(
            op["operation_id"],
            authenticated_at=op["started_at"] + 1,
            hydrated_at=op["started_at"] + 2,
            submitted_at=op["started_at"] + 3,
            committed_at=op["started_at"] + 5,
            completed_at=op["started_at"] + 8,
            state="complete",
        )

    report = timing_report(db)
    assert report["attempts_considered"] == 1
    assert report["stages"]["start_to_auth"]["p50_s"] == 1.0
    assert report["stages"]["submit_to_commit"]["p50_s"] == 2.0
    assert report["stages"]["commit_to_complete"]["p50_s"] == 3.0
