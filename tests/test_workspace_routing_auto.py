"""Routing and auto policy remain provenance-safe and loop-bounded."""
from unittest.mock import patch
import pytest
from re_gpt.workspace_routing import parse_route
from re_gpt.workspace_auto_policy import queue_auto_turn, maybe_enqueue_local_review
from re_gpt.workspace_projection import read_workspace_conversation

def test_provider_mentions_are_explicit():
    assert parse_route("@local review").targets==("local",)
    assert parse_route("@both evaluate").targets==("chatgpt","local")
    assert parse_route("@ao prove").targets==("ao",)
    assert parse_route("normal text").targets==("chatgpt",)
    assert not parse_route("normal text").explicit
    with pytest.raises(ValueError): parse_route("@local")

def test_local_first_preserves_order_without_remote_graph_promotion():
    sent=[]
    def fake_queue(cid,prompt,**kwargs):
        sent.append(kwargs["provider"])
        return {"job_id":str(len(sent))}
    with patch("re_gpt.workspace_auto_policy.get_mode",return_value="local_first"),patch(
        "re_gpt.pg_turn_worker.queue_turn",side_effect=fake_queue):
        result=queue_auto_turn("cid","task")
    assert sent==["local","chatgpt"]
    assert len(result["jobs"])==2

def test_local_after_is_idempotently_parent_keyed():
    archive={"messages":[{"role":"assistant","text":"Result"}]}
    with patch("re_gpt.workspace_auto_policy.get_mode",return_value="local_after"),patch(
        "re_gpt.pg_turn_worker.queue_turn",return_value={"job_id":"parent:auto-review"}) as queued:
        result=maybe_enqueue_local_review("cid",parent_job_id="parent",
             read=lambda cid:archive)
    assert result["job_id"]=="parent:auto-review"
    assert queued.call_args.kwargs["job_id"]=="parent:auto-review"
    assert queued.call_args.kwargs["provider"]=="local"

def test_workspace_local_output_is_a_distinct_event_lane():
    remote={"conversation_id":"cid","messages":[{"role":"assistant","text":"Remote"}]}
    def replay(cid,after,limit):
        if after: return {"events":[],"next_cursor":after}
        return {"events":[{"event_id":1,"operation_id":"local-job",
            "kind":"agent_response","payload":{"body":"Review"}}],"next_cursor":1}
    combined=read_workspace_conversation("cid",read=lambda cid:remote,replay=replay)
    assert combined["messages"][0]["text"]=="Remote"
    assert combined["messages"][1]["source"]=="workspace_agent_event"
    assert combined["workspace_messages_included"]==1
