"""Offline PostgreSQL conversation projection contract."""
from unittest.mock import Mock
from re_gpt.pg_conversation_read import list_pg_conversations, read_pg_conversation


def fake(rows):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchall.return_value = rows
    conn = Mock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_catalog_uses_bound_parameters_and_closes_connection():
    conn, cursor = fake([("abc", "Math", "2026-10-30", 8)])
    result = list_pg_conversations(query="math", connect=lambda: conn)
    assert result[0]["id"] == "abc"
    assert result[0]["message_count"] == 8
    sql, params = cursor.execute.call_args.args
    assert "%s" in sql and "math" not in sql
    assert params[:3] == ("math", "%math%", "%math%")
    conn.close.assert_called_once()


def test_conversation_preserves_message_text_and_provenance():
    conn, cursor = fake([("node:a", "assistant", r"\\[x^2\\]", "Title",
                          "message:abc:a", None, "active", "2026-10-30")])
    result = read_pg_conversation("abc", connect=lambda: conn)
    assert result["messages"][0]["text"] == r"\\[x^2\\]"
    assert result["messages"][0]["message_ref"] == "message:abc:a"
    assert cursor.execute.call_args.args[1] == ("conversation:abc", 10000)


def test_rejects_invalid_pagination_without_connecting():
    import pytest
    with pytest.raises(ValueError):
        list_pg_conversations(limit=-1, connect=lambda: None)
