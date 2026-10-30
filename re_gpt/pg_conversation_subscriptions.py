"""TUI-independent PG change subscriber.

Consumes durable operation events and corpus revisions. Unlike the older
network watcher, it does not fetch a graph, refresh an auth session or
distinguish changes by which browser/client originated them.
"""
from __future__ import annotations
import threading
from .pg_conversation_read import _connect
from .pg_operation_events import events_after

def source_revision(conversation_id,*,connect=_connect):
    conn=connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT md5(coalesce(string_agg(
                message_ref || ':' || coalesce(source_revision_ref,''),
                ',' ORDER BY message_ref), ''))
                FROM corpus.chat_archive_message
                WHERE conversation_ref=%s AND identity_verified IS TRUE""",
                ("conversation:"+conversation_id,))
            row=cur.fetchone()
            return row[0] if row else None
    finally: conn.close()

class PGConversationSubscriber:
    def __init__(self,*,interval=1.5,revision_reader=source_revision,event_reader=events_after):
        self.interval=interval
        self.revision_reader=revision_reader
        self.event_reader=event_reader
        self._callbacks={}
        self._cursors={}
        self._revisions={}
        self._stop=threading.Event()
        self._thread=None
        self._lock=threading.Lock()

    def watch(self,conversation_id,callback):
        with self._lock:
            self._callbacks[conversation_id]=callback
            if self._thread is None:
                self._thread=threading.Thread(target=self._run,daemon=True,
                    name="re-gpt-pg-event-subscriptions")
                self._thread.start()

    def unwatch(self,conversation_id):
        with self._lock:
            self._callbacks.pop(conversation_id,None)

    def close(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.wait(self.interval):
            with self._lock: watched=list(self._callbacks.items())
            for cid,callback in watched:
                try:
                    cursor=self._cursors.get(cid,0)
                    batch=self.event_reader(cid,after=cursor,limit=250)
                    events=batch["events"]
                    revision=self.revision_reader(cid)
                    changed=cid in self._revisions and self._revisions[cid]!=revision
                    if events or changed:
                        callback({"conversation_id":cid,"events":events,
                                  "revision":revision,"source_changed":changed})
                    # Advance only after callback accepted the batch.
                    self._cursors[cid]=batch["next_cursor"]
                    self._revisions[cid]=revision
                except Exception:
                    continue
