from __future__ import annotations

import re_gpt.mcp_server as mcp_server


class FakeService:
    def __init__(self) -> None:
        self.calls = []

    def new_conversation(self, prompt, **kwargs):
        self.calls.append(("new", prompt, kwargs))
        return {"state": "complete", "conversation_id": "new-id"}

    def branch_from_node(self, conversation_id, parent_node_id, prompt, **kwargs):
        self.calls.append(("branch", conversation_id, parent_node_id, prompt, kwargs))
        return {"state": "complete", "conversation_id": conversation_id}

    def regenerate(self, conversation_id, user_node_id, **kwargs):
        self.calls.append(("regenerate", conversation_id, user_node_id, kwargs))
        return {"state": "complete", "conversation_id": conversation_id}

    def edit_and_branch(self, conversation_id, user_node_id, prompt, **kwargs):
        self.calls.append(("edit", conversation_id, user_node_id, prompt, kwargs))
        return {"state": "complete", "conversation_id": conversation_id}


def test_mcp_graph_mutations_route_to_shared_service(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(mcp_server, "_service", lambda db_path=None: service)

    assert mcp_server.chatgpt_new("hello", follow=True)["conversation_id"] == "new-id"
    mcp_server.chatgpt_branch("c1", "a0", "branch", attachments=["a.txt"], follow=True)
    mcp_server.chatgpt_regenerate("c1", "u0", follow=True)
    mcp_server.chatgpt_edit_branch("c1", "u0", "replacement", attachments=["b.txt"], follow=True)

    assert service.calls[0][0:2] == ("new", "hello")
    assert service.calls[1][0:4] == ("branch", "c1", "a0", "branch")
    assert service.calls[1][4]["attachments"] == ["a.txt"]
    assert service.calls[2][0:3] == ("regenerate", "c1", "u0")
    assert service.calls[3][0:4] == ("edit", "c1", "u0", "replacement")


def test_mcp_continue_defaults_to_api_first_without_dom_fallback(monkeypatch) -> None:
    seen = {}

    class ContinueService:
        def continue_thread(self, *args, **kwargs):
            seen.update(kwargs)
            return {"state": "complete"}

    monkeypatch.setattr(mcp_server, "_service", lambda db_path=None: ContinueService())
    mcp_server.chatgpt_continue("c1", "hello")
    assert seen["write_transport"] == "async"
    assert seen["dom_fallback"] is False
