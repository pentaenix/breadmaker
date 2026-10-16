from __future__ import annotations

"""History process coordination and explicit History landmark service.

This module owns the process-local Remote History queue/worker and the small explicit
History commit service used by feature domains. Current project persistence remains in focused ``storage`` owners; remote replication
is best-effort durability downstream of Current.
"""

import os
import queue
import threading
import time
from pathlib import Path

from backend import diagnostics
from backend.environment import runtime
from storage import history as storage_history
from workspace.models import WorkspaceError

_REMOTE_HISTORY_QUEUE: queue.Queue[tuple[str,str|None,str]] | None = None
_REMOTE_HISTORY_STOP_TOKEN: threading.Event | None = None
_REMOTE_HISTORY_THREAD: threading.Thread | None = None
_REMOTE_HISTORY_RETRY_SECONDS = 60.0
_REMOTE_HISTORY_STARTUP_DELAY_SECONDS = 0.5


def _has_local_history_for_project(project_id:str) -> bool:
    """Cheap local-only test used before automatic remote reconciliation."""
    try:
        rec=runtime.projects.get(str(project_id))
        if not rec:return False
        revisions=Path(str(rec['path'])).resolve()/'revision_history'/'v1'/'revisions'
        if not revisions.is_dir():return False
        with os.scandir(revisions) as entries:
            return any(entry.is_file() and entry.name.endswith('.revision.json') for entry in entries)
    except Exception:
        return False


def queue_remote_history(project_id:str, revision_id:str|None=None, *, reason:str='event') -> None:
    """Queue best-effort Remote History work without blocking Current."""
    work_queue=_REMOTE_HISTORY_QUEUE
    if work_queue is None:return
    try:work_queue.put_nowait((str(project_id),str(revision_id) if revision_id else None,str(reason or 'event')))
    except Exception:pass



def _remote_history_worker_loop(stop_event:threading.Event, work_queue:queue.Queue[tuple[str,str|None,str]], startup_project_id:str|None=None) -> None:
    pending:dict[str,float]={}
    retry_attempts:dict[str,int]={}
    startup_due=(time.monotonic()+_REMOTE_HISTORY_STARTUP_DELAY_SECONDS) if startup_project_id else None
    while not stop_event.is_set():
        item=None
        now_mono=time.monotonic()
        deadlines=list(pending.values())
        if startup_due is not None:deadlines.append(startup_due)
        wait_timeout=.5
        if deadlines:wait_timeout=max(0.0,min(.5,min(deadlines)-now_mono))
        try:item=work_queue.get(timeout=wait_timeout)
        except queue.Empty:
            now_mono=time.monotonic()
            if startup_project_id and startup_due is not None and startup_due<=now_mono:
                item=(startup_project_id,None,'startup');startup_project_id=None;startup_due=None
            else:
                due=next((pid for pid,deadline in sorted(pending.items(),key=lambda kv:kv[1]) if deadline<=now_mono),None)
                if due is not None:pending.pop(due,None);item=(due,None,'retry')
        if item is None:continue
        if stop_event.is_set():
            try:work_queue.task_done()
            except Exception:pass
            break
        project_id,revision_id,reason=item
        try:project_record=runtime.projects.get(project_id)
        except WorkspaceError:project_record=None
        if project_record is None:
            pending.pop(project_id,None);retry_attempts.pop(project_id,None)
            try:work_queue.task_done()
            except Exception:pass
            continue
        try:
            if stop_event.is_set():continue
            if reason=='startup' and runtime.projects.active_id()!=project_id:continue
            result=runtime.history.sync_remote(project_id,revision_id=revision_id)
            if stop_event.is_set():continue
            if result.get('failed'):
                pending[project_id]=time.monotonic()+_REMOTE_HISTORY_RETRY_SECONDS
                retry_attempts[project_id]=int(retry_attempts.get(project_id,0))+1
                diagnostics.log_event('REMOTE_HISTORY_001','Remote History replication failed for one or more destinations without affecting Current, local History, or Recovery.',level='WARNING',context={'project_id':project_id,'revision_id':revision_id or 'all','failed_destinations':len(result.get('failed') or [])},data_safe=True)
                diagnostics.log_storage_event('STORAGE_REMOTE_HISTORY_RETRY','Remote History redundancy retry scheduled.',level='WARNING',project_id=project_id,revision_id=revision_id or 'all',state_transition='SYNC_FAILED_RETRY_SCHEDULED',verification_outcome='failed',retry_attempt=retry_attempts[project_id],queue_depth=work_queue.qsize(),reason=reason)
            else:
                pending.pop(project_id,None);retry_attempts.pop(project_id,None)
                diagnostics.log_storage_event('STORAGE_REMOTE_HISTORY_SYNC','Remote History synchronization verified.',project_id=project_id,revision_id=revision_id or 'all',state_transition='SYNC_VERIFIED',verification_outcome='verified',retry_attempt=0,queue_depth=work_queue.qsize(),reason=reason)
                maintenance=runtime.history.run_maintenance(project_id)
                if maintenance.get('failed'):
                    diagnostics.log_event('HISTORY_GC_001','History maintenance retained data rather than deleting under uncertainty.',level='WARNING',context={'project_id':project_id,'failures':len(maintenance.get('failed') or [])},data_safe=True)
                else:
                    diagnostics.log_storage_event('STORAGE_HISTORY_MAINTENANCE','History maintenance completed without reducing Current or Recovery authority.',project_id=project_id,state_transition='MAINTENANCE_VERIFIED',verification_outcome='verified',queue_depth=work_queue.qsize(),scope='local+remote')
        except Exception as exc:
            pending[project_id]=time.monotonic()+_REMOTE_HISTORY_RETRY_SECONDS
            retry_attempts[project_id]=int(retry_attempts.get(project_id,0))+1
            diagnostics.log_storage_event('STORAGE_REMOTE_HISTORY_WORKER_RETRY','Remote History worker retry scheduled after an isolated provider failure.',level='WARNING',project_id=project_id,revision_id=revision_id or 'all',state_transition='WORKER_FAILURE_RETRY_SCHEDULED',verification_outcome='failed',retry_attempt=retry_attempts[project_id],queue_depth=work_queue.qsize(),reason=reason)
            diagnostics.log_event('REMOTE_HISTORY_002','Remote History replication worker failed without affecting Current, local History, or Recovery.',level='WARNING',error=exc,context={'project_id':project_id,'revision_id':revision_id or 'all'},data_safe=True)
        finally:
            try:work_queue.task_done()
            except Exception:pass


def start_remote_history_worker() -> None:
    global _REMOTE_HISTORY_THREAD,_REMOTE_HISTORY_STOP_TOKEN,_REMOTE_HISTORY_QUEUE
    stop_event=threading.Event();work_queue:queue.Queue[tuple[str,str|None,str]]=queue.Queue()
    _REMOTE_HISTORY_STOP_TOKEN=stop_event;_REMOTE_HISTORY_QUEUE=work_queue
    project_id='' if runtime.sessions.requires_authentication else str(runtime.projects.active_id() or '')
    startup_project_id=project_id if project_id and _has_local_history_for_project(project_id) else None
    _REMOTE_HISTORY_THREAD=threading.Thread(target=_remote_history_worker_loop,args=(stop_event,work_queue,startup_project_id),name='breadmaker-remote-history',daemon=True)
    _REMOTE_HISTORY_THREAD.start()


def request_remote_history_stop() -> None:
    stop_token=_REMOTE_HISTORY_STOP_TOKEN
    if stop_token is not None:stop_token.set()


def stop_remote_history_worker(*,timeout:float=1.0) -> bool:
    global _REMOTE_HISTORY_THREAD,_REMOTE_HISTORY_STOP_TOKEN,_REMOTE_HISTORY_QUEUE
    request_remote_history_stop();worker=_REMOTE_HISTORY_THREAD
    if worker and worker.is_alive():worker.join(timeout=timeout)
    stopped=worker is None or not worker.is_alive()
    if _REMOTE_HISTORY_THREAD is worker:
        _REMOTE_HISTORY_THREAD=None;_REMOTE_HISTORY_STOP_TOKEN=None;_REMOTE_HISTORY_QUEUE=None
    return stopped


def commit(label,kind='edit',dedupe=True):
    """Create an explicit immutable History landmark for high-risk writer actions."""
    _=dedupe
    try:
        revision=storage_history.capture_local_revision(label=(label or 'History')[:180],creation_reason=(kind or 'edit')[:80],summary=storage_history.history_current_summary())
        try:queue_remote_history(str(revision.get('workspace_project_id') or ''),str(revision.get('revision_id') or '') or None)
        except Exception:pass
        return str(revision.get('revision_id') or '')
    except Exception as exc:
        diagnostics.log_event('LOCAL_HISTORY_EXPLICIT_001','Explicit History landmark could not be published. Current was not changed.',level='WARNING',error=exc,data_safe=True)
        return ''
