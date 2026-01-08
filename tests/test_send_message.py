
import pytest
from re_gpt.sync_chatgpt import SyncChatGPT
from re_gpt.storage import ConversationStorage, NullConversationStorage
from re_gpt.cli import select_conversation, stream_response
from re_gpt.utils import get_session_token

def test_send_message():
    """
    Tests sending a message and receiving a response.
    """
    try:
        token = get_session_token()
    except Exception:
        pytest.skip("Session token not found, skipping test.")

    storage = NullConversationStorage()
    with SyncChatGPT(session_token=token) as chatgpt:
        conversation = chatgpt.create_new_conversation(model="gpt-3.5")
        prompt = "Hello, world!"
        response_generator = conversation.chat(prompt)
        response = "".join(part.get("content", "") for part in response_generator)
        
        assert response, "Received an empty response."
        print(f"Received response: {response}")
