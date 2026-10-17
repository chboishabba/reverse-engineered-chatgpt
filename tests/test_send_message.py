import os

import pytest

from re_gpt.storage import NullConversationStorage
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.utils import get_session_token


@pytest.mark.legacy_direct_post
def test_send_message():
    """Opt-in diagnostic for the historical backend POST transport.

    This is intentionally not a production acceptance test. A 429/CF response
    here says nothing about the browser-owned DOM mutation path.
    """
    if os.environ.get("REGPT_LEGACY_DIRECT_POST") != "1":
        pytest.skip("legacy direct-POST transport is diagnostic-only; set REGPT_LEGACY_DIRECT_POST=1 to probe it")

    try:
        token = get_session_token()
    except Exception:
        pytest.skip("Session token not found, skipping legacy direct-POST probe.")

    storage = NullConversationStorage()
    with SyncChatGPT(session_token=token) as chatgpt:
        conversation = chatgpt.create_new_conversation(model="gpt-3.5")
        response_generator = conversation.chat("Hello, world!")
        response = "".join(part.get("content", "") for part in response_generator)
        assert response, "Received an empty response from legacy direct POST."
