from re_gpt import SyncChatGPT

# consts
session_token = "__Secure-next-auth.session-token here"

# Create ChatGPT instance using the session token
with SyncChatGPT(session_token=session_token) as chatgpt:
    conversations = chatgpt.list_all_conversations()

    for idx, conv in enumerate(conversations):
        print(f"{idx}: {conv['title']}")

    selected = int(input("Select conversation number: "))
    conversation = chatgpt.get_conversation(conversations[selected]["id"])

    prompt = input("Enter your prompt: ")
    for message_chunk in conversation.chat(prompt):
        print(message_chunk["content"], flush=True, end="")
