from __future__ import annotations

"""Backup Destination inventory/discovery and restore endpoints."""

import time
from fastapi import APIRouter, HTTPException
from backend.environment import runtime
from runtime.native import require_native_backups
from runtime.native import require_native_projects
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _bound_user_id, _public_project_record, _require_hosted_project_management
from workspace.models import WorkspaceError

router = APIRouter()

@router.get("/api/backup-destinations/{destination_id}/backups")
def backup_destination_backups(destination_id:str):
    _require_hosted_project_management()
    try:return runtime.backups.destination_backups(destination_id)
    except WorkspaceError as exc:raise HTTPException(409,detail=str(exc))

@router.get("/api/backup-destinations/{destination_id}/discover")
def backup_destination_discover(destination_id:str):
    _require_hosted_project_management()
    try:return runtime.backups.discover_destination_backups(destination_id)
    except WorkspaceError as exc:raise HTTPException(409,detail=str(exc))

@router.post("/api/backup-destinations/{destination_id}/backups/{filename}/restore-as-project")
def backup_destination_restore(destination_id:str,filename:str):
    _require_hosted_project_management()
    try:
        with require_native_backups(runtime).materialize_destination_backup(destination_id,filename) as path:
            info=require_native_projects(runtime).verify_package(path);stamp=time.strftime('%Y-%m-%d %H%M',time.localtime(path.stat().st_mtime))
            rec=require_native_projects(runtime).import_package(path,name_override=f"{info.get('name') or 'Recovered Project'} — Recovered {stamp}",owner_user_id=_bound_user_id() or None)
        _activate_project(rec['id']);return {"ok":True,"project":_public_project_record(rec),"reload":True}
    except (WorkspaceError,OSError) as exc:raise HTTPException(400,detail=f"RECOVERY_003: {exc}")
