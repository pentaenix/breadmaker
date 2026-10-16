from __future__ import annotations

"""Asynchronous publication of committed Current state into immutable local History.

This module owns the local History queue, worker, debounce policy, and activity barrier.
Current commits never depend on History publication succeeding.
"""

import queue
import sqlite3
import threading
import time
from pathlib import Path

from backend import diagnostics
from backend import history_runtime
from backend.environment import runtime
from runtime.native import require_native_history
from revision_store.local_store import StaleRevisionPublication
from storage import history as storage_history
from storage import mutations as storage_mutations


_LOCAL_HISTORY_QUEUE: queue.Queue[dict] | None = None
_LOCAL_HISTORY_STOP_TOKEN: threading.Event | None = None
_LOCAL_HISTORY_THREAD: threading.Thread | None = None
_LOCAL_HISTORY_DEBOUNCE_SECONDS = 90.0
_LOCAL_HISTORY_ACTIVITY_LOCK = threading.RLock()

def _canonical_commit_history_observer(event:dict) -> None:
    """Non-blocking bridge from committed Current to asynchronous History."""
    work_queue=_LOCAL_HISTORY_QUEUE
    if work_queue is None:return
    try:work_queue.put_nowait(dict(event))
    except Exception:pass

# Storage commits are authoritative; History observes them asynchronously. Registration
# belongs beside the observer so extracting this module cannot silently sever the bridge.
storage_mutations.register_canonical_commit_listener(_canonical_commit_history_observer)

def _publish_local_history_event(event:dict) -> None:
    with _LOCAL_HISTORY_ACTIVITY_LOCK:
        return _publish_local_history_event_locked(event)

def _publish_local_history_event_locked(event:dict) -> None:
    """Publish one debounced canonical commit into immutable local History.

    This function is deliberately downstream of Current. It may fail, retry later, or
    be skipped as stale; none of those outcomes can roll back the committed project.
    """
    wid=str(event.get('workspace_project_id') or '')
    if not wid:return
    try:
        root=Path(str(event.get('project_root') or '')).resolve()
        db=root/'project.sqlite3'
        if not db.is_file():return
        con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)
        try:
            row=con.execute('SELECT project_generation FROM projects ORDER BY id LIMIT 1').fetchone()
            generation=int(row[0] or 0) if row else 0
        finally:con.close()
        if generation<=0:return
        operation=str(event.get('operation') or 'canonical_commit')[:80]
        store=require_native_history(runtime).revision_store_for(root)
        revision=store.capture(
            label=str(event.get('history_label') or 'Automatic History')[:180],creation_reason=str(event.get('history_reason') or 'canonical_commit')[:80],
            summary={**storage_history.history_current_summary_for(root),'operation':operation},
            expected_generation=generation,
        )
        # Remote redundancy is intentionally best-effort here. Startup reconciliation
        # can publish this immutable local History later if shutdown has already stopped
        # the remote worker.
        history_runtime.queue_remote_history(wid,str(revision.get('revision_id') or '') or None,reason='canonical_commit')
    except StaleRevisionPublication:
        # A newer canonical commit won while this capture was freezing. The newest
        # pending event (or startup reconciliation) will publish the authoritative state.
        return
    except Exception as exc:
        diagnostics.log_event('LOCAL_HISTORY_COMMIT_001','Canonical Current committed safely but its asynchronous History landmark could not be published.',level='WARNING',error=exc,data_safe=True)

def _local_history_worker_loop(stop_event:threading.Event, work_queue:queue.Queue[dict]) -> None:
    pending:dict[str,tuple[float,dict]]={}
    while True:
        if stop_event.is_set():
            # Shutdown collapses every queued/pending commit to the newest event per
            # workspace and publishes it immediately. This preserves short structural
            # sessions without turning History into a prerequisite for Current commit.
            while True:
                try:item=work_queue.get_nowait()
                except queue.Empty:break
                try:
                    wid=str(item.get('workspace_project_id') or '')
                    if wid:pending[wid]=(0.0,dict(item))
                finally:
                    try:work_queue.task_done()
                    except Exception:pass
            for wid,(_deadline,event) in list(pending.items()):
                pending.pop(wid,None)
                _publish_local_history_event(event)
            return

        now_mono=time.monotonic();deadlines=[row[0] for row in pending.values()]
        timeout=max(0.0,min(.5,min(deadlines)-now_mono)) if deadlines else .5
        item=None
        try:item=work_queue.get(timeout=timeout)
        except queue.Empty:pass
        if item is not None:
            wid=str(item.get('workspace_project_id') or '')
            if wid:pending[wid]=(time.monotonic()+_LOCAL_HISTORY_DEBOUNCE_SECONDS,dict(item))
            try:work_queue.task_done()
            except Exception:pass
        now_mono=time.monotonic()
        due=[(wid,row) for wid,row in pending.items() if row[0]<=now_mono]
        for wid,(_deadline,event) in due:
            pending.pop(wid,None)
            _publish_local_history_event(event)

def _start_local_history_worker() -> None:
    global _LOCAL_HISTORY_QUEUE,_LOCAL_HISTORY_STOP_TOKEN,_LOCAL_HISTORY_THREAD
    stop_event=threading.Event();work_queue:queue.Queue[dict]=queue.Queue()
    _LOCAL_HISTORY_STOP_TOKEN=stop_event;_LOCAL_HISTORY_QUEUE=work_queue
    _LOCAL_HISTORY_THREAD=threading.Thread(target=_local_history_worker_loop,args=(stop_event,work_queue),name='breadmaker-local-history',daemon=True)
    _LOCAL_HISTORY_THREAD.start()

def request_stop() -> None:
    stop=_LOCAL_HISTORY_STOP_TOKEN
    if stop is not None:
        stop.set()

def stop_worker(*, timeout: float = 2.0) -> bool:
    global _LOCAL_HISTORY_THREAD,_LOCAL_HISTORY_STOP_TOKEN,_LOCAL_HISTORY_QUEUE
    worker=_LOCAL_HISTORY_THREAD
    if worker and worker.is_alive():
        worker.join(timeout=timeout)
    stopped=worker is None or not worker.is_alive()
    if stopped and _LOCAL_HISTORY_THREAD is worker:
        _LOCAL_HISTORY_THREAD=None
        _LOCAL_HISTORY_STOP_TOKEN=None
        _LOCAL_HISTORY_QUEUE=None
    return stopped

def activity_barrier():
    """Return the lock that excludes local History publication during privacy erasure."""
    return _LOCAL_HISTORY_ACTIVITY_LOCK
