"""PostgreSQL-owned event journal for concurrent conversation operations.

No TUI/global active-thread state; cursor-scoped replay and immutable attribution.
This is a durable event *primitive*, not a detached worker supervisor.
"""
from __future__ import annotations
import json
from .pg_conversation_read import _connect

class EventConflict(RuntimeError):
    pass

def ensure_event_schema(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute("""CREATE TABLE IF NOT EXISTS ops.chat_operation_event (
          event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          conversation_id TEXT NOT NULL,
          operation_id TEXT NOT NULL,
          operation_seq BIGINT NOT NULL CHECK (operation_seq >= 1),
          event_kind TEXT NOT NULL,
          event_payload JSONB NOT NULL,
          observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (operation_id, operation_seq)
        )""")
        cur.execute("""CREATE INDEX IF NOT EXISTS chat_operation_event_thread_cursor
          ON ops.chat_operation_event(conversation_id,event_id)""")

def append_event(conversation_id, operation_id, seq, kind, payload, *, connect=_connect):
    if not isinstance(conversation_id,str) or not conversation_id or len(conversation_id)>150:
        raise ValueError("invalid conversation ID")
    if not isinstance(operation_id,str) or not operation_id or len(operation_id)>150:
        raise ValueError("invalid operation ID")
    if type(seq) is not int or seq<1: raise ValueError("invalid operation sequence")
    if not isinstance(kind,str) or not kind or len(kind)>80: raise ValueError("invalid event kind")
    if not isinstance(payload,dict): raise ValueError("event payload must be object")
    wire=json.dumps(payload,ensure_ascii=False)
    if len(wire)>1_000_000: raise ValueError("event payload too large")
    conn=connect()
    try:
        with conn.transaction():
            ensure_event_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops.chat_operation_event
                  (conversation_id,operation_id,operation_seq,event_kind,event_payload)
                  VALUES (%s,%s,%s,%s,%s::jsonb)
                  ON CONFLICT (operation_id,operation_seq) DO NOTHING
                  RETURNING event_id""",(conversation_id,operation_id,seq,kind,wire))
                inserted=cur.fetchone()
                if inserted: return {"event_id":inserted[0],"inserted":True}
                cur.execute("""SELECT event_id,conversation_id,event_kind,event_payload
                  FROM ops.chat_operation_event WHERE operation_id=%s AND operation_seq=%s""",
                  (operation_id,seq))
                old=cur.fetchone()
                prior=old[3]
                if isinstance(prior,str): prior=json.loads(prior)
                if old[1]!=conversation_id or old[2]!=kind or prior!=payload:
                    raise EventConflict("same operation/sequence has different event data")
                return {"event_id":old[0],"inserted":False}
    finally: conn.close()

def events_after(conversation_id, after=0, limit=100, *, connect=_connect):
    if not isinstance(conversation_id,str) or not conversation_id:
        raise ValueError("conversation required")
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=500:
        raise ValueError("invalid replay window")
    conn=connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT event_id,operation_id,operation_seq,event_kind,
                             event_payload,observed_at
              FROM ops.chat_operation_event WHERE conversation_id=%s AND event_id>%s
              ORDER BY event_id LIMIT %s""",(conversation_id,after,limit))
            rows=cur.fetchall()
        return {"conversation_id":conversation_id,"events":[
          {"event_id":r[0],"operation_id":r[1],"operation_seq":r[2],
           "kind":r[3],"payload":json.loads(r[4]) if isinstance(r[4],str) else r[4],
           "observed_at":str(r[5])} for r in rows],
          "next_cursor":rows[-1][0] if rows else after}
    finally: conn.close()
