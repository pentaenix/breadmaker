from __future__ import annotations

"""Verified Recovery backup creation, integrity, listing, and restore endpoints."""

import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend import diagnostics
from backend.environment import _HOSTED_LIMITS, runtime
from runtime.native import require_native_backups
from runtime.native import require_native_projects
from backend.project_activation import activate_project as _activate_project
from backend.recovery_runtime import (
    _BACKUP_DESTINATION_WAKE,
    current_recovery_mode as _current_recovery_mode,
    _ensure_recovery_scheduler,
)
from backend.request_context import (
    _bound_project,
    _bound_user_id,
    _enforce_hosted_new_project_quota,
    _public_project_record,
    _require_hosted_project_management,
)
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from workspace.models import WorkspaceError

router = APIRouter()

def _maybe_external_backup(reason:str, *, force:bool=False):
    rec=_bound_project()
    if rec is None and not runtime.sessions.requires_authentication:
        rec=runtime.projects.active()
    if not rec:return None
    project_id=str(rec['id']);project_root=Path(str(rec['path'])).resolve()
    if not force:
        # Bind Recovery scheduling to the project that actually mutated, even when
        # the local shell has switched its globally selected project meanwhile.
        try:
            generation=storage_mutations.current_project_generation()
            _ensure_recovery_scheduler(project_id,project_root,generation,time.monotonic()).observe(project_id,generation,time.monotonic())
        except Exception as exc:
            diagnostics.log_event('BACKUP_DESTINATION_SCHEDULE_001','Could not observe project generation for Recovery scheduling; Current was not changed.',level='WARNING',error=exc,context={'project_id':project_id},data_safe=True)
        _BACKUP_DESTINATION_WAKE.set();return {'scheduled':True}
    try:
        result=runtime.backups.catch_up(rec['id'],reason=reason,force=True,quiet_seconds=0)
        if result.get('failed'):
            diagnostics.log_event('BACKUP_DESTINATION_001','One or more backup destinations could not be updated. The active project was not changed.',level='WARNING',context={'failed':[item.get('id') for item in result['failed']]})
        return result
    except Exception as exc:
        diagnostics.log_event('BACKUP_DESTINATION_002','Backup destination catch-up could not be completed. The active project was not changed.',level='ERROR',error=exc,data_safe=True);return None

def verified_full_backup(reason:str, *, project_root:Path|None=None) -> Path:
    """Create a complete project package and prove it can be verified before use."""
    path=storage_recovery.backup_project(reason,project_root=project_root)
    if path is None:
        raise HTTPException(500,detail="BACKUP_CREATE_002: No active project database was available to back up.")
    try:
        require_native_projects(runtime).verify_package(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        occurrence=diagnostics.log_event("BACKUP_VERIFY_001","A newly created backup failed verification and was removed.",level="ERROR",error=exc,data_safe=True)
        raise HTTPException(500,detail={"code":"BACKUP_VERIFY_001","message":f"Backup verification failed. Your active project was not changed. Diagnostic {occurrence}.","params":{"diagnostic":occurrence}})
    return path

@router.get("/api/integrity/deep")
def deep_integrity(): return storage_integrity.deep_integrity_check()

@router.post("/api/backup")
def manual_backup():
    _require_hosted_project_management()
    p=verified_full_backup('manual_backup')
    external=_maybe_external_backup('manual_backup',force=True)
    diagnostics.log_event("BACKUP_CREATE_001","Created and verified a full project backup.",context={"file":p.name})
    return {"ok":True,"file":p.name,"external":external}

def _available_recovery_backup_dir() -> Path:
    """Return the active project's Recovery directory without requiring its DB to open."""
    recovery_mode=_current_recovery_mode()
    if recovery_mode:
        target=str(recovery_mode.get('active_project_id') or '')
        candidates=runtime.projects.recovery_candidates()
        row=next((item for item in candidates if str(item.get('id') or '')==target),None)
        if row and row.get('path'):
            return Path(str(row['path'])).resolve()/'backups'
        try:
            rec=runtime.projects.get(target) if target else None
        except Exception:
            rec=None
        if rec and rec.get('path'):
            return Path(str(rec['path'])).resolve()/'backups'
    return require_native_backups(runtime).recovery_dir

@router.get("/api/backups")
def list_backups():
    _require_hosted_project_management()
    rows=[]
    backup_dir=_available_recovery_backup_dir()
    if backup_dir.exists():
        for path in sorted(backup_dir.glob("*.breadmaker"),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                info=require_native_projects(runtime).verify_package(path); info.update({"file":path.name,"verified":True})
            except Exception as exc:
                info={"file":path.name,"verified":False,"error":str(exc),"bytes":path.stat().st_size}
            rows.append(info)
    return rows

@router.post("/api/backups/{filename}/restore-as-project")
def restore_backup_as_project(filename:str):
    _require_hosted_project_management()
    if Path(filename).name!=filename or not filename.endswith('.breadmaker'): raise HTTPException(400,"RECOVERY_001: Invalid backup name")
    backup_dir=_available_recovery_backup_dir()
    path=(backup_dir/filename).resolve()
    try: path.relative_to(backup_dir.resolve())
    except ValueError: raise HTTPException(400,"RECOVERY_001: Invalid backup name")
    if not path.is_file(): raise HTTPException(404,"RECOVERY_002: Backup not found")
    try:
        info=require_native_projects(runtime).verify_package(path)
        owner_user_id=_bound_user_id() or None
        estimate=0
        if _HOSTED_LIMITS is not None:
            with zipfile.ZipFile(path) as archive:estimate=sum(int(item.file_size or 0) for item in archive.infolist() if not item.is_dir())
        _enforce_hosted_new_project_quota(owner_user_id or '',estimated_bytes=estimate)
        stamp=time.strftime('%Y-%m-%d %H%M',time.localtime(path.stat().st_mtime))
        rec=require_native_projects(runtime).import_package(path,name_override=f"{info.get('name') or 'Recovered Project'} — Recovered {stamp}",owner_user_id=owner_user_id)
        _activate_project(rec['id'])
        diagnostics.log_event("RECOVERY_RESTORE_001","Restored a verified backup as a separate project.",context={"backup":filename,"project_id":rec['id']})
        return {"ok":True,"project":_public_project_record(rec),"reload":True}
    except WorkspaceError as exc:
        raise HTTPException(400,f"RECOVERY_003: {exc}")
