from __future__ import annotations

"""Recovery state and trash restore HTTP endpoints."""

from fastapi import APIRouter, HTTPException, Request
from backend.environment import runtime
from backend.project_activation import activate_project as _activate_project
from backend.recovery_runtime import current_recovery_mode as _current_recovery_mode
from backend.request_context import _profile_request_user_id, _public_project_record, _public_project_records, _require_hosted_project_management
from workspace.models import WorkspaceError

router = APIRouter()

@router.get("/api/recovery/status")
def recovery_status(request:Request):
    _require_hosted_project_management()
    user_id=_profile_request_user_id(request) or runtime.identity.active_profile_id()
    candidates=[] if runtime.sessions.requires_authentication else runtime.projects.recovery_candidates()
    return {"recovery":_current_recovery_mode(),"projects":_public_project_records(runtime.projects.list(user_id=user_id)),"candidates":_public_project_records(candidates),"trash":_public_project_records(runtime.projects.list_trash(user_id=user_id))}

@router.post("/api/trash/{trash_id}/restore")
def restore_trash_project(trash_id:str,request:Request):
    user_id=_profile_request_user_id(request) or runtime.identity.active_profile_id()
    try:
        rec=runtime.projects.restore_from_trash(trash_id,user_id=user_id); _activate_project(rec['id'],user_id=user_id); return {"ok":True,"project":_public_project_record(rec),"reload":True}
    except WorkspaceError as exc: raise HTTPException(409,detail=str(exc))
