from __future__ import annotations

"""Explicit Recovery-backup scheduling endpoint."""

import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from backend.recovery_runtime import _BACKUP_DESTINATION_WAKE, _ensure_recovery_scheduler
from backend.request_context import _bound_project, _bound_project_id, _require_hosted_project_management
from storage import context as storage_context
from storage import mutations as storage_mutations

router = APIRouter()

@router.post("/api/recovery-backups/update")
def recovery_backups_update():
    _require_hosted_project_management()
    """Writer-facing Update backups action; scheduling remains coordinator-owned."""
    project_id=_bound_project_id()
    rec=_bound_project()
    if not project_id or not rec:raise HTTPException(404,detail='PROJECT_CONTEXT_REQUIRED')
    with storage_context.project_context(Path(str(rec['path'])).resolve(),project_id):
        generation=storage_mutations.current_project_generation()
        scheduler=_ensure_recovery_scheduler(project_id,Path(str(rec['path'])).resolve(),generation,time.monotonic())
        scheduler.request_manual()
    _BACKUP_DESTINATION_WAKE.set()
    return {'ok':True,'scheduled':True}
