"""Persisted per-thread auto-response routing policy, without recursive agents."""
from __future__ import annotations
from .pg_conversation_read import _connect, read_pg_conversation

MODES={"off","local_first","parallel","local_after"}

def ensure_policy(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute("""CREATE TABLE IF NOT EXISTS ops.chat_auto_policy(
          conversation_id TEXT PRIMARY KEY,
          mode TEXT NOT NULL CHECK(mode IN ('off','local_first','parallel','local_after')),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")

def set_mode(cid,mode,*,connect=_connect):
    if not isinstance(cid,str) or not cid or len(cid)>150:
        raise ValueError("conversation ID required")
    if mode not in MODES: raise ValueError("invalid auto mode")
    conn=connect()
    try:
        with conn.transaction():
            ensure_policy(conn)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops.chat_auto_policy(conversation_id,mode)
                  VALUES (%s,%s) ON CONFLICT(conversation_id) DO UPDATE SET
                  mode=EXCLUDED.mode,updated_at=now()""",(cid,mode))
    finally: conn.close()
    return {"conversation_id":cid,"mode":mode}

def get_mode(cid,*,connect=_connect):
    conn=connect()
    try:
        with conn.transaction():
            ensure_policy(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT mode FROM ops.chat_auto_policy WHERE conversation_id=%s",(cid,))
                row=cur.fetchone()
        return row[0] if row else "off"
    finally: conn.close()

def queue_auto_turn(cid,prompt,*,connect=_connect):
    from .pg_turn_worker import queue_turn
    mode=get_mode(cid,connect=connect)
    if mode=="off" or mode=="local_after":
        targets=("chatgpt",)
    elif mode=="local_first":
        targets=("local","chatgpt")
    else:
        targets=("chatgpt","local")
    return {"mode":mode,"jobs":[queue_turn(cid,prompt,provider=provider,connect=connect)
                                  for provider in targets]}

def maybe_enqueue_local_review(cid,*,connect=_connect,read=read_pg_conversation):
    if get_mode(cid,connect=connect)!="local_after": return None
    from .pg_turn_worker import queue_turn
    source=read(cid)
    last=next((m for m in reversed(source.get("messages",[]))
               if m.get("role")=="assistant" and m.get("text")),None)
    if last is None: return None
    return queue_turn(cid,
        "Review the following completed ChatGPT response, with source attribution. "
        "Respond independently, do not impersonate the remote assistant:\n\n"+
        str(last["text"]),provider="local",actor="auto_review",connect=connect)
