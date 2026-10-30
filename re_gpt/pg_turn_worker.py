"""Durable PG queue for independent ChatGPT conversation workers.

The terminal or MCP may enqueue work. A *separate* worker process performs
MutationService dispatch, so closing/switching the TUI never kills the job.
Unknown outcomes are held for reconciliation and never automatically retried.
"""
from __future__ import annotations
import json
import time
import uuid
from .pg_conversation_read import _connect
from .pg_operation_events import append_event
from .mutation_service import MutationService

def ensure_job_schema(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute("""CREATE TABLE IF NOT EXISTS ops.chat_conversation_job (
          job_id TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL,
          actor TEXT NOT NULL,
          prompt TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN
            ('queued','running','complete','failed_precommit','commit_unknown')),
          mutation_operation_id TEXT,
          result JSONB,
          error_class TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at TIMESTAMPTZ,
          finished_at TIMESTAMPTZ,
          UNIQUE (conversation_id,job_id)
        )""")
        cur.execute("""CREATE INDEX IF NOT EXISTS chat_conversation_job_queue
          ON ops.chat_conversation_job(created_at) WHERE state='queued'""")

def queue_turn(conversation_id,prompt,*,actor="human",job_id=None,connect=_connect):
    if not isinstance(conversation_id,str) or not conversation_id or len(conversation_id)>150:
        raise ValueError("conversation_id required; create new conversations via existing service")
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>200000:
        raise ValueError("invalid prompt")
    if not isinstance(actor,str) or not actor or len(actor)>80:
        raise ValueError("invalid actor")
    identifier=job_id or str(uuid.uuid4())
    if not isinstance(identifier,str) or not identifier or len(identifier)>150:
        raise ValueError("invalid job_id")
    conn=connect()
    try:
        with conn.transaction():
            ensure_job_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops.chat_conversation_job
                   (job_id,conversation_id,actor,prompt,state)
                   VALUES (%s,%s,%s,%s,'queued')
                   ON CONFLICT (job_id) DO NOTHING RETURNING job_id""",
                   (identifier,conversation_id,actor,prompt))
                created=cur.fetchone()
                if not created:
                    cur.execute("""SELECT conversation_id,actor,prompt
                      FROM ops.chat_conversation_job WHERE job_id=%s""",(identifier,))
                    old=cur.fetchone()
                    if old!=(conversation_id,actor,prompt):
                        raise ValueError("conflicting idempotency key")
    finally: conn.close()
    return {"job_id":identifier,"conversation_id":conversation_id,
            "state":"queued","created":bool(created)}

def claim_next(*,connect=_connect):
    conn=connect()
    try:
        with conn.transaction():
            ensure_job_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""SELECT job_id,conversation_id,prompt,actor
                  FROM ops.chat_conversation_job j WHERE state='queued'
                    AND NOT EXISTS (
                      SELECT 1 FROM ops.chat_conversation_job active
                      WHERE active.conversation_id=j.conversation_id
                        AND active.state IN ('running','commit_unknown'))
                  ORDER BY created_at,job_id
                  FOR UPDATE SKIP LOCKED LIMIT 1""")
                row=cur.fetchone()
                if not row: return None
                cur.execute("""UPDATE ops.chat_conversation_job
                  SET state='running',started_at=now()
                  WHERE job_id=%s""",(row[0],))
                return {"job_id":row[0],"conversation_id":row[1],
                        "prompt":row[2],"actor":row[3]}
    finally: conn.close()

def finish(job_id,state,*,receipt=None,error_class=None,connect=_connect):
    if state not in {"complete","failed_precommit","commit_unknown"}:
        raise ValueError("invalid terminal state")
    conn=connect()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("""UPDATE ops.chat_conversation_job
                   SET state=%s,result=%s::jsonb,error_class=%s,finished_at=now(),
                       mutation_operation_id=%s
                   WHERE job_id=%s AND state='running'""",
                   (state,json.dumps(receipt or {}),error_class,
                    (receipt or {}).get("operation_id"),job_id))
                if cur.rowcount!=1: raise RuntimeError("job no longer running")
    finally: conn.close()

def process_one(*,connect=_connect,service_factory=MutationService):
    job=claim_next(connect=connect)
    if job is None: return None
    cid=job["conversation_id"]; jid=job["job_id"]
    seq=0
    def emit(event):
        nonlocal seq
        seq+=1
        append_event(cid,jid,seq,str(event.get("kind") or "progress"),
                     dict(event),connect=connect)
    try:
        result=service_factory().continue_thread(
            cid,job["prompt"],write_transport="async",dom_fallback=False,
            follow=True,follow_timeout=300,progress_callback=emit)
        state=str(result.get("state") or "commit_unknown")
        final="complete" if state=="complete" else "commit_unknown"
        # Do not persist whole unvalidated provider result in job receipt.
        receipt={"operation_id":result.get("operation_id"),
                 "conversation_id":result.get("conversation_id") or cid,
                 "state":state}
        finish(jid,final,receipt=receipt,connect=connect)
        emit({"kind":"job_terminal","state":final,**receipt})
        return {"job_id":jid,"state":final,"events":seq}
    except Exception as exc:
        # A transport exception does not prove the server rejected the write.
        finish(jid,"commit_unknown",error_class=type(exc).__name__,connect=connect)
        emit({"kind":"job_terminal","state":"commit_unknown",
              "error_class":type(exc).__name__})
        return {"job_id":jid,"state":"commit_unknown","events":seq}

def serve_forever(*,interval=1.0):
    while True:
        result=process_one()
        if result is None: time.sleep(interval)

def main():
    import argparse
    parser=argparse.ArgumentParser(description="Durable PG ChatGPT turn worker")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if args.once: print(json.dumps(process_one()))
    else: serve_forever()

if __name__=="__main__": main()
