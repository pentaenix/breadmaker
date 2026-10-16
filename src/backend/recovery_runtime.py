from __future__ import annotations

"""Process-local Recovery and Backup Destination coordination."""

import threading
import time
from pathlib import Path

from backups.scheduler import RecoveryBackupScheduler
from backend import diagnostics
from backend.dependencies import feature_call
from backend.environment import runtime
from runtime.native import require_native_projects
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from workspace.models import WorkspaceError

_RECOVERY_MODE: dict | None = None
_BACKUP_DESTINATION_STOP = threading.Event()
_BACKUP_DESTINATION_WAKE = threading.Event()
_BACKUP_DESTINATION_RECONCILE = threading.Event()
_BACKUP_DESTINATION_RECONCILE_LOCK = threading.Lock()
_BACKUP_DESTINATION_RECONCILE_PROJECTS: set[str] = set()
_BACKUP_DESTINATION_THREAD: threading.Thread | None = None
_BACKUP_DESTINATION_MAINTENANCE_SECONDS = 5 * 60.0
_BACKUP_DESTINATION_MAINTENANCE_DUE: dict[str,float] = {}
_RECOVERY_BACKUP_SCHEDULER = RecoveryBackupScheduler()
_RECOVERY_BACKUP_SCHEDULERS: dict[str,RecoveryBackupScheduler] = {}
_RECOVERY_BACKUP_SCHEDULERS_LOCK = threading.RLock()
verified_full_backup = feature_call('durability.backups','verified_full_backup')


def _current_recovery_mode() -> dict | None:
    """Return current Recovery state without copying a reassignable module scalar."""
    return _RECOVERY_MODE

current_recovery_mode=_current_recovery_mode


def set_recovery_mode(value:dict|None) -> None:
    global _RECOVERY_MODE
    _RECOVERY_MODE=value

def _destination_retry_required(result: dict) -> bool:
    """Return whether destination reconciliation should be retried later.

    A disconnected local/removable drive or temporarily unreachable remote provider is
    normal and must not be recorded as a destructive/write failure. It *does* remain
    pending work whenever that destination is behind Current, so the coordinator keeps
    a bounded retry clock alive and notices the destination when it returns.
    """
    if result.get('failed'):
        return True
    for row in result.get('destinations') or []:
        if not isinstance(row,dict) or row.get('up_to_date'):
            continue
        if row.get('available') is False and not row.get('attention') and str(row.get('reason') or '')=='not_connected':
            return True
    return False


def _recovery_scheduler_for(project_id:str, *, make_current:bool=False) -> RecoveryBackupScheduler:
    """Return one scheduler per project while preserving the legacy active alias."""
    global _RECOVERY_BACKUP_SCHEDULER
    pid=str(project_id or '')
    if not pid: return _RECOVERY_BACKUP_SCHEDULER
    with _RECOVERY_BACKUP_SCHEDULERS_LOCK:
        current=_RECOVERY_BACKUP_SCHEDULER
        if current.project_id==pid:
            scheduler=current
        else:
            scheduler=_RECOVERY_BACKUP_SCHEDULERS.get(pid)
            if scheduler is None:
                scheduler=RecoveryBackupScheduler()
                _RECOVERY_BACKUP_SCHEDULERS[pid]=scheduler
        if make_current:
            if current.project_id:
                _RECOVERY_BACKUP_SCHEDULERS[current.project_id]=current
            _RECOVERY_BACKUP_SCHEDULER=scheduler
            _RECOVERY_BACKUP_SCHEDULERS[pid]=scheduler
        return scheduler

def _forget_recovery_project(project_id:str) -> None:
    """Drop coordinator-only state for a project that no longer exists locally."""
    global _RECOVERY_BACKUP_SCHEDULER
    pid=str(project_id or '')
    if not pid:return
    with _RECOVERY_BACKUP_SCHEDULERS_LOCK:
        _RECOVERY_BACKUP_SCHEDULERS.pop(pid,None)
        if _RECOVERY_BACKUP_SCHEDULER.project_id==pid:
            _RECOVERY_BACKUP_SCHEDULER=RecoveryBackupScheduler()
    _BACKUP_DESTINATION_MAINTENANCE_DUE.pop(pid,None)
    with _BACKUP_DESTINATION_RECONCILE_LOCK:
        _BACKUP_DESTINATION_RECONCILE_PROJECTS.discard(pid)
        if not _BACKUP_DESTINATION_RECONCILE_PROJECTS:_BACKUP_DESTINATION_RECONCILE.clear()


def _tracked_recovery_project_ids() -> list[str]:
    """Return only projects that actually need coordinator attention.

    9.4 introduced one scheduler per request-addressable project. Keeping every clean
    scheduler forever turned project switching into permanent background/shutdown work.
    The on-disk recovery_state.json remains the durable authority, so inactive clean
    schedulers can be forgotten and rebuilt if that project is touched again.
    """
    # Only the local shell has a meaningful process-global selected project.
    # Hosted account sessions track Recovery from explicit project mutations /
    # retry state; never let the last account to touch the process become
    # background maintenance authority for everyone else.
    active='' if runtime.sessions.requires_authentication else str(runtime.projects.active_id() or '')
    with _BACKUP_DESTINATION_RECONCILE_LOCK:
        reconcile_ids=set(_BACKUP_DESTINATION_RECONCILE_PROJECTS)
    ids=set(reconcile_ids)
    if active:ids.add(active)
    with _RECOVERY_BACKUP_SCHEDULERS_LOCK:
        candidates=dict(_RECOVERY_BACKUP_SCHEDULERS)
        if _RECOVERY_BACKUP_SCHEDULER.project_id:
            candidates[_RECOVERY_BACKUP_SCHEDULER.project_id]=_RECOVERY_BACKUP_SCHEDULER
        for pid,scheduler in candidates.items():
            if pid in ids or scheduler.has_pending_work():
                ids.add(pid)
            elif pid!=active:
                _RECOVERY_BACKUP_SCHEDULERS.pop(pid,None)
                _BACKUP_DESTINATION_MAINTENANCE_DUE.pop(pid,None)
    return sorted(ids)

def _reset_recovery_runtime_state() -> None:
    """Reset rebuildable coordinator memory for a fresh application lifespan.

    Recovery protection itself is persisted per project. Retry timers and scheduler
    objects are process/lifespan state and must not leak across an in-process restart
    (or TestClient lifespan), where they can make unrelated projects look pending.
    """
    global _RECOVERY_BACKUP_SCHEDULER
    with _RECOVERY_BACKUP_SCHEDULERS_LOCK:
        _RECOVERY_BACKUP_SCHEDULERS.clear()
        _RECOVERY_BACKUP_SCHEDULER=RecoveryBackupScheduler()
    _BACKUP_DESTINATION_MAINTENANCE_DUE.clear()
    with _BACKUP_DESTINATION_RECONCILE_LOCK:
        _BACKUP_DESTINATION_RECONCILE_PROJECTS.clear()
        _BACKUP_DESTINATION_RECONCILE.clear()

def _ensure_recovery_scheduler(project_id:str, project_root:Path, generation:int, now_mono:float, *, make_current:bool=False) -> RecoveryBackupScheduler:
    scheduler=_recovery_scheduler_for(project_id,make_current=make_current)
    if scheduler.project_id!=str(project_id):
        protected=storage_recovery.recovery_scheduler_protected_generation(project_root,generation,initialize=True)
        scheduler.reset(str(project_id),generation,now_mono,protected_generation=protected)
    return scheduler

def _backup_destination_worker_loop():
    """Generation-aware Recovery coordinator for every project with pending work.

    The local shell still has one selected project, but request-scoped mutations may
    target other projects. Recovery scheduling therefore follows project identity, not
    process-global UI selection. Every project gets an independent scheduler state.
    """
    while not _BACKUP_DESTINATION_STOP.is_set():
        _BACKUP_DESTINATION_WAKE.wait(1.0)
        _BACKUP_DESTINATION_WAKE.clear()
        if _BACKUP_DESTINATION_STOP.is_set():break
        for project_id in _tracked_recovery_project_ids():
            if _BACKUP_DESTINATION_STOP.is_set():break
            try:rec=runtime.projects.get(project_id)
            except WorkspaceError:
                _forget_recovery_project(project_id)
                continue
            if not rec:
                _forget_recovery_project(project_id)
                continue
            project_root=Path(str(rec['path'])).resolve()
            try:
                with storage_context.project_context(project_root,project_id):
                    generation=storage_mutations.current_project_generation()
                    now_mono=time.monotonic()
                    active_record=None if runtime.sessions.requires_authentication else runtime.projects.active()
                    active_project_id=str((active_record or {}).get('id') or '') if isinstance(active_record,dict) else ''
                    scheduler=_ensure_recovery_scheduler(project_id,project_root,generation,now_mono,make_current=(active_project_id==str(project_id)))
                    scheduler.observe(project_id,generation,now_mono)

                    with _BACKUP_DESTINATION_RECONCILE_LOCK:
                        reconcile_requested=project_id in _BACKUP_DESTINATION_RECONCILE_PROJECTS
                        if reconcile_requested:_BACKUP_DESTINATION_RECONCILE_PROJECTS.discard(project_id)
                        if not _BACKUP_DESTINATION_RECONCILE_PROJECTS:_BACKUP_DESTINATION_RECONCILE.clear()
                    if reconcile_requested:
                        try:
                            reconcile=runtime.backups.catch_up(project_id,reason='destination_reconcile',force=False,quiet_seconds=0)
                            if _destination_retry_required(reconcile):scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                            else:scheduler.mark_destination_success(project_id=project_id)
                        except Exception as exc:
                            scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                            diagnostics.log_event('BACKUP_DESTINATION_WORKER_004','Destination reconcile failed without changing local Recovery or Current.',level='WARNING',error=exc,data_safe=True)

                    maintenance_due=_BACKUP_DESTINATION_MAINTENANCE_DUE.get(project_id,0.0)
                    if now_mono>=maintenance_due and not reconcile_requested:
                        _BACKUP_DESTINATION_MAINTENANCE_DUE[project_id]=now_mono+_BACKUP_DESTINATION_MAINTENANCE_SECONDS
                        try:
                            maintenance=runtime.backups.catch_up(project_id,reason='destination_maintenance',force=False,quiet_seconds=0)
                            if _destination_retry_required(maintenance):scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                            else:scheduler.mark_destination_success(project_id=project_id)
                        except Exception as exc:
                            scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                            diagnostics.log_event('BACKUP_DESTINATION_MAINTENANCE_001','Destination maintenance probe failed without changing Current or local Recovery.',level='WARNING',error=exc,data_safe=True)

                    decision=scheduler.due(time.monotonic())
                    if decision is None or decision.project_id!=project_id:continue
                    if decision.retry_only:
                        try:result=runtime.backups.catch_up(project_id,reason=decision.trigger,force=False,quiet_seconds=0)
                        except Exception as exc:
                            scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                            diagnostics.log_event('BACKUP_DESTINATION_WORKER_002','A backup destination retry failed without affecting local Recovery or Current.',level='WARNING',error=exc,data_safe=True)
                            continue
                        if _destination_retry_required(result):scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                        else:scheduler.mark_destination_success(project_id=project_id)
                        continue

                    try:
                        local_package=verified_full_backup(decision.trigger,project_root=project_root)
                        local_info=require_native_projects(runtime).inspect_package_metadata(local_package)
                        local_generation=int(local_info.get('project_generation') or 0)
                        if local_generation<=0:raise RuntimeError('RECOVERY_BACKUP_GENERATION_MISSING')
                        scheduler.mark_local_success(local_generation,time.monotonic(),project_id=project_id)
                    except Exception as exc:
                        scheduler.mark_local_failure(time.monotonic(),project_id=project_id)
                        diagnostics.log_event('BACKUP_DESTINATION_WORKER_001','Local Recovery backup scheduling failed without changing the project.',level='WARNING',error=exc,data_safe=True)
                        continue

                    try:result=runtime.backups.catch_up(project_id,reason=decision.trigger,force=True,quiet_seconds=0)
                    except Exception as exc:
                        scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                        diagnostics.log_event('BACKUP_DESTINATION_WORKER_002','A backup destination update failed after local Recovery succeeded.',level='WARNING',error=exc,data_safe=True)
                        continue
                    if _destination_retry_required(result):scheduler.mark_destination_failure(time.monotonic(),project_id=project_id)
                    else:scheduler.mark_destination_success(project_id=project_id)
            except Exception as exc:
                diagnostics.log_event('BACKUP_DESTINATION_WORKER_003','Recovery coordinator encountered an unexpected failure without changing Current.',level='WARNING',error=exc,context={'project_id':project_id},data_safe=True)


def _best_effort_shutdown_recovery() -> None:
    """Protect dirty tracked projects during a normal bounded shutdown.

    Abrupt termination is handled by Current/History durability. This path is only a
    normal-shutdown optimization, but request-scoped projects must not lose that
    optimization merely because another project is selected in the local shell.
    """
    project_ids=_tracked_recovery_project_ids()
    active=None if runtime.sessions.requires_authentication else runtime.projects.active_id()
    if active and str(active) not in project_ids:project_ids.append(str(active))
    for project_id in project_ids:
        try:rec=runtime.projects.get(project_id)
        except WorkspaceError:
            _forget_recovery_project(project_id)
            continue
        if not rec:
            _forget_recovery_project(project_id)
            continue
        project_root=Path(str(rec['path'])).resolve();now_mono=time.monotonic()
        try:
            with storage_context.project_context(project_root,project_id):
                generation=storage_mutations.current_project_generation()
                scheduler=_ensure_recovery_scheduler(project_id,project_root,generation,now_mono)
                scheduler.observe(project_id,generation,now_mono)
                if scheduler.dirty_generation>scheduler.last_success_generation:
                    package=verified_full_backup('best_effort_shutdown',project_root=project_root)
                    info=require_native_projects(runtime).inspect_package_metadata(package)
                    protected=int(info.get('project_generation') or 0)
                    if protected>0:scheduler.mark_local_success(protected,now_mono,project_id=project_id)
        except Exception as exc:
            diagnostics.log_event('BACKUP_SHUTDOWN_001','Best-effort shutdown Recovery package could not be completed. Current was not changed.',level='WARNING',error=exc,context={'project_id':project_id},data_safe=True)
        try:
            result=runtime.backups.catch_up(project_id,reason='best_effort_shutdown',force=False,quiet_seconds=0)
            scheduler=_recovery_scheduler_for(project_id)
            if _destination_retry_required(result):scheduler.mark_destination_failure(now_mono,project_id=project_id)
            else:scheduler.mark_destination_success(project_id=project_id)
        except Exception as exc:
            diagnostics.log_event('BACKUP_SHUTDOWN_002','Best-effort shutdown destination catch-up failed without affecting Current or local Recovery.',level='WARNING',error=exc,context={'project_id':project_id},data_safe=True)


def _start_backup_destination_worker():
    global _BACKUP_DESTINATION_THREAD
    _BACKUP_DESTINATION_STOP.clear()
    if _BACKUP_DESTINATION_THREAD and _BACKUP_DESTINATION_THREAD.is_alive(): return
    _BACKUP_DESTINATION_THREAD=threading.Thread(target=_backup_destination_worker_loop,name='breadmaker-backup-destinations',daemon=True)
    _BACKUP_DESTINATION_THREAD.start()


def request_backup_destination_stop() -> None:
    _BACKUP_DESTINATION_STOP.set();_BACKUP_DESTINATION_WAKE.set()


def stop_backup_destination_worker(*,timeout:float=2.0) -> bool:
    global _BACKUP_DESTINATION_THREAD
    request_backup_destination_stop();worker=_BACKUP_DESTINATION_THREAD
    if worker and worker.is_alive():worker.join(timeout=timeout)
    stopped=worker is None or not worker.is_alive()
    if stopped and _BACKUP_DESTINATION_THREAD is worker:_BACKUP_DESTINATION_THREAD=None
    return stopped
