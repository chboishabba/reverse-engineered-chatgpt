from pathlib import Path


def test_extract_messages_handles_none_create_time():
    # Provide a dummy session token so importing the example does not fail
    config = Path("config.ini")
    config.write_text("[session]\ntoken=dummy\n")
    try:
        from examples.select_chat import _extract_messages
    finally:
        # Clean up the temporary config file
        config.unlink()

    chat = {
        "mapping": {
            "1": {
                "message": {
                    "author": {"role": "system"},
                    "content": {"parts": ["System message"]},
                    "create_time": None,
                }
            },
            "2": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["User message"]},
                    "create_time": 100,
                }
            },
        }
    }

    messages = _extract_messages(chat)
    assert messages[0]["create_time"] == 0
    assert [m["content"] for m in messages] == ["System message", "User message"]
