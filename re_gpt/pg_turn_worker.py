"""Durable PG queue for independent ChatGPT conversation workers.

The terminal or MCP may enqueue work. A *separate* worker process performs
MutationService dispatch, so closing/switching the TUI never kills the job.
Unknown outcomes are held for reconciliation and never automatically retried.
"""
from __future__ import annotations
import json
import os
import subprocess
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
        cur.execute("ALTER TABLE ops.chat_conversation_job ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'chatgpt'")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS chat_job_one_active
            ON ops.chat_conversation_job(conversation_id)
            WHERE state IN ('running','commit_unknown')""")
        cur.execute("""CREATE INDEX IF NOT EXISTS chat_conversation_job_queue
          ON ops.chat_conversation_job(created_at) WHERE state='queued'""")

def queue_turn(conversation_id,prompt,*,actor="human",job_id=None,provider="chatgpt",connect=_connect):
    if not isinstance(conversation_id,str) or not conversation_id or len(conversation_id)>150:
        raise ValueError("conversation_id required; create new conversations via existing service")
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>200000:
        raise ValueError("invalid prompt")
    if not isinstance(actor,str) or not actor or len(actor)>80:
        raise ValueError("invalid actor")
    if provider not in {"chatgpt","local"}: raise ValueError("unsupported execution provider")
    identifier=job_id or str(uuid.uuid4())
    if not isinstance(identifier,str) or not identifier or len(identifier)>150:
        raise ValueError("invalid job_id")
    conn=connect()
    try:
        with conn.transaction():
            ensure_job_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops.chat_conversation_job
                   (job_id,conversation_id,actor,prompt,state,provider)
                   VALUES (%s,%s,%s,%s,'queued',%s)
                   ON CONFLICT (job_id) DO NOTHING RETURNING job_id""",
                   (identifier,conversation_id,actor,prompt,provider))
                created=cur.fetchone()
                if not created:
                    cur.execute("""SELECT conversation_id,actor,prompt,provider
                      FROM ops.chat_conversation_job WHERE job_id=%s""",(identifier,))
                    old=cur.fetchone()
                    if old!=(conversation_id,actor,prompt,provider):
                        raise ValueError("conflicting idempotency key")
    finally: conn.close()
    return {"job_id":identifier,"conversation_id":conversation_id,
            "state":"queued","provider":provider,"created":bool(created)}

def claim_next(*,connect=_connect):
    conn=connect()
    try:
        with conn.transaction():
            ensure_job_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""SELECT job_id,conversation_id,prompt,actor,provider
                  FROM ops.chat_conversation_job j WHERE state='queued'
                    AND NOT EXISTS (
                      SELECT 1 FROM ops.chat_conversation_job active
                      WHERE active.conversation_id=j.conversation_id
                        AND active.state IN ('running','commit_unknown'))
                  ORDER BY created_at,job_id
                  FOR UPDATE SKIP LOCKED LIMIT 1""")
                row=cur.fetchone()
                if not row: return None
                # Claim one conversation at a time across independent workers.
                cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))",(row[1],))
                lock=cur.fetchone()
                if not lock or not lock[0]: return None
                cur.execute("""SELECT 1 FROM ops.chat_conversation_job
                  WHERE conversation_id=%s AND state IN ('running','commit_unknown')
                  LIMIT 1""",(row[1],))
                if cur.fetchone(): return None
                cur.execute("""UPDATE ops.chat_conversation_job
                  SET state='running',started_at=now()
                  WHERE job_id=%s""",(row[0],))
                return {"job_id":row[0],"conversation_id":row[1],
                        "prompt":row[2],"actor":row[3],"provider":row[4]}
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

def job_status(job_id,*,connect=_connect):
    if not isinstance(job_id,str) or not job_id or len(job_id)>150:
        raise ValueError("invalid job ID")
    conn=connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT conversation_id,actor,provider,state,
                mutation_operation_id,error_class,created_at,started_at,finished_at
                FROM ops.chat_conversation_job WHERE job_id=%s""",(job_id,))
            row=cur.fetchone()
        if row is None: raise KeyError("job not found")
        return {"job_id":job_id,"conversation_id":row[0],"actor":row[1],
                "provider":row[2],"state":row[3],"mutation_operation_id":row[4],
                "error_class":row[5],"created_at":str(row[6]),
                "started_at":str(row[7]) if row[7] else None,
                "finished_at":str(row[8]) if row[8] else None}
    finally: conn.close()

def cancel_queued(job_id,*,connect=_connect):
    if not isinstance(job_id,str) or not job_id or len(job_id)>150:
        raise ValueError("invalid job ID")
    conn=connect()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("""UPDATE ops.chat_conversation_job
                    SET state='failed_precommit',error_class='cancelled_before_dispatch',
                        finished_at=now()
                    WHERE job_id=%s AND state='queued' RETURNING conversation_id""",
                    (job_id,))
                row=cur.fetchone()
        return {"job_id":job_id,"cancelled":bool(row),
                "reason":"only queued, unstarted jobs can be cancelled"}
    finally: conn.close()

def execute_local_job(prompt):
    """Opt-in configured agent CLI; input via stdin, never shell."""
    raw=os.environ.get("ITIR_LOCAL_AGENT_ARGV")
    if not raw:
        raise RuntimeError("local agent adapter not configured")
    argv=json.loads(raw)
    if (not isinstance(argv,list) or not argv or len(argv)>24
        or any(not isinstance(item,str) or not item or len(item)>500 for item in argv)):
        raise ValueError("invalid ITIR_LOCAL_AGENT_ARGV")
    env={name:value for name,value in os.environ.items()
         if name in {"HOME","PATH","LANG","LC_ALL","XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_RUNTIME_DIR"}}
    completed=subprocess.run(argv,input=prompt,text=True,capture_output=True,
                             timeout=900,check=False,env=env)
    if completed.returncode:
        raise RuntimeError("local agent execution failed")
    return completed.stdout[:500000]


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
        if job.get("provider")=="local":
            response=execute_local_job(job["prompt"])
            emit({"kind":"agent_response","provider":"local","body":response})
            finish(jid,"complete",receipt={"state":"complete","provider":"local"},connect=connect)
            emit({"kind":"job_terminal","state":"complete","provider":"local"})
            return {"job_id":jid,"state":"complete","events":seq}
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
        sync_state="pending"
        if final=="complete":
            try:
                from .pg_hydration import hydrate_one
                hydrate_one(cid)
                sync_state="hydrated"
            except Exception:
                sync_state="sync_pending"
        auto_review=None
        if final=="complete" and sync_state=="hydrated":
            try:
                from .workspace_auto_policy import maybe_enqueue_local_review
                auto_review=maybe_enqueue_local_review(cid,parent_job_id=jid)
            except Exception:
                auto_review=None
        emit({"kind":"job_terminal","state":final,
              "sync_state":sync_state,"auto_review_queued":bool(auto_review),**receipt})
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
    parser.add_argument("--workers",type=int,default=2)
    args=parser.parse_args()
    if not 1<=args.workers<=20: parser.error("workers must be 1..20")
    if args.once:
        print(json.dumps(process_one()))
    elif args.workers==1:
        serve_forever()
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks=[pool.submit(serve_forever) for _ in range(args.workers)]
            for future in tasks: future.result()

if __name__=="__main__": main()
