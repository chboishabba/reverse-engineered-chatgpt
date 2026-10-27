from re_gpt.operation_store import MutationOperationStore
from re_gpt.stream_follow import SSEProgressDecoder


def test_sse_activity_persists_without_sensitive_text(tmp_path):
    decoder = SSEProgressDecoder()
    frames = (
        'data: {"message":{"id":"a","author":{"role":"assistant"},'
        '"recipient":"github_connector","content":{"content_type":"text","parts":[]}}}\n\n'
        'data: {"message":{"id":"b","author":{"role":"assistant"},'
        '"channel":"analysis","content":{"content_type":"text","parts":["private thought"]}}}\n\n'
    )
    events = decoder.feed(frames[:45].encode()) + decoder.feed(frames[45:].encode())
    assert any(e["kind"] == "tool_invocation" for e in events)
    assert any(e["kind"] == "thinking" for e in events)
    with MutationOperationStore(tmp_path / "state.sqlite3") as store:
        op = store.create(operation="continue", conversation_id="c", prompt="secret")
        for event in events:
            store.record_activity(op["operation_id"], event)
        live = store.live_activity(op["operation_id"])
        assert live["last_activity_at"] is not None
        assert live["seconds_since_last_activity"] >= 0
        assert live["recent_activities"]
        assert "private thought" not in str(live)
        assert "secret" not in str(live)


def test_legacy_schema_migrates(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE mutation_attempts (operation_id TEXT PRIMARY KEY)")
    with MutationOperationStore(path) as store:
        columns = {r[1] for r in store.connection.execute("PRAGMA table_info(mutation_attempts)")}
        assert {"last_activity_at", "current_stage", "activity_summary"} <= columns
