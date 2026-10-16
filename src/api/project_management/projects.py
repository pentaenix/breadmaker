from __future__ import annotations

"""Project creation, activation, and naming HTTP surface."""

from fastapi import APIRouter, HTTPException

from backend import diagnostics
from api.project_management.contracts import WorkspaceProjectCreate, WorkspaceProjectRename
from backend.environment import runtime
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _bound_user_id, _enforce_hosted_new_project_quota, _public_project_record, _require_project_permission
from workspace.models import WorkspaceError

router=APIRouter()

@router.post("/api/projects")
def create_workspace_project(payload: WorkspaceProjectCreate):
    owner_user_id=_bound_user_id() or None
    _enforce_hosted_new_project_quota(owner_user_id or '')
    # Do not publish a project as the process-global active project until its
    # database has been initialized and validated. The Recovery worker observes the
    # active registry concurrently; publishing the empty shell first lets it race
    # activation and open the not-yet-created SQLite path.
    rec=runtime.projects.create(payload.name,activate=False,owner_user_id=owner_user_id)
    rec=_activate_project(rec["id"],user_id=owner_user_id)
    diagnostics.log_event("PROJECT_CREATE_001","Created a new Breadmaker project.",context={"project_id":rec["id"],"name":rec["name"]})
    return {"project":_public_project_record(rec),"reload":True}

@router.post("/api/projects/{project_id}/activate")
def activate_workspace_project(project_id: str):
    _require_project_permission('project.read',project_id)
    try: rec=_activate_project(project_id)
    except WorkspaceError as exc: raise HTTPException(404,detail=str(exc))
    return {"project":_public_project_record(rec),"reload":True}

@router.patch("/api/projects/{project_id}")
def rename_workspace_project(project_id: str, payload: WorkspaceProjectRename):
    _require_project_permission('project.edit',project_id)
    try: rec=runtime.projects.rename(project_id,payload.name)
    except WorkspaceError as exc: raise HTTPException(404,detail=str(exc))
    return _public_project_record(rec)
