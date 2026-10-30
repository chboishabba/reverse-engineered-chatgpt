"""Read-only shared conversation view: canonical remote chat + agent event lane.

Local agent output is never inserted into ChatGPT's canonical remote graph.
"""
from __future__ import annotations
from .pg_conversation_read import read_pg_conversation
from .pg_operation_events import events_after

def read_workspace_conversation(conversation_id, *,read=read_pg_conversation,
                                replay=events_after, max_events=5000):
    result=read(conversation_id)
    messages=list(result["messages"])
    cursor=0
    seen=0
    while seen<max_events:
        try:
            batch=replay(conversation_id,after=cursor,limit=min(500,max_events-seen))
        except Exception as exc:
            # Pre-migration installations must still read the PG corpus.
            if getattr(exc, "sqlstate", None) == "42P01":
                break
            raise
        events=batch["events"]
        if not events: break
        for event in events:
            if event.get("kind")=="agent_response":
                payload=event.get("payload") or {}
                text=payload.get("body")
                if isinstance(text,str):
                    messages.append({"id":"agent:"+str(event["event_id"]),
                        "role":"assistant","author":{"role":"assistant"},
                        "text":"[Local agent | job "+event["operation_id"]+"]\n\n"+text,
                        "content":{"parts":[text]},
                        "source":"workspace_agent_event","operation_id":event["operation_id"]})
        seen+=len(events)
        next_cursor=batch["next_cursor"]
        if next_cursor<=cursor: break
        cursor=next_cursor
        if len(events)<500: break
    return {**result,"messages":messages,
            "workspace_event_cursor":cursor,
            "workspace_messages_included":len(messages)-len(result["messages"])}
