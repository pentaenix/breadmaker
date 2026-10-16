from __future__ import annotations

"""Project membership administration and privacy-safe member projections."""

from fastapi import APIRouter, HTTPException

from api.project_management.contracts import ProjectMemberAdd
from backend.environment import runtime
from backend.request_context import _require_project_permission
from workspace.models import WorkspaceError

router=APIRouter()

def _public_project_members(project_id:str,rows:list[dict]) -> list[dict]:
    """Expose stable account ids only to actors allowed to manage membership.

    member_id is project-local and is sufficient for ordinary collaboration UI. A
    globally stable usr_ account id is a correlation primitive and should not be
    disclosed to every Editor merely because they can read the project. Local desktop
    profiles remain one trusted installation; hosted account sessions get the privacy
    boundary.
    """
    expose_user_id=True
    if runtime.sessions.requires_authentication:
        try:_require_project_permission('project.members.manage',project_id)
        except HTTPException:expose_user_id=False
    out=[]
    for row in rows or []:
        item=dict(row)
        if not expose_user_id:item.pop('user_id',None)
        out.append(item)
    return out

@router.get('/api/projects/{project_id}/members')
def list_project_members(project_id:str):
    _require_project_permission('project.read',project_id)
    return {'members':_public_project_members(project_id,runtime.projects.members(project_id))}

@router.post('/api/projects/{project_id}/members')
def add_project_member(project_id:str,body:ProjectMemberAdd):
    _require_project_permission('project.members.manage',project_id)
    try:member=runtime.projects.add_member(project_id,body.user_id,body.role)
    except WorkspaceError as exc:
        code=str(exc)
        if code=='PROFILE_NOT_FOUND':raise HTTPException(404,detail='Profile not found') from exc
        if code=='PROJECT_MEMBER_EXISTS':raise HTTPException(409,detail='This profile is already a member of the project') from exc
        raise HTTPException(400,detail=code) from exc
    return {'member':member,'members':_public_project_members(project_id,runtime.projects.members(project_id))}

@router.delete('/api/projects/{project_id}/members/{user_id}')
def remove_project_member(project_id:str,user_id:str):
    _require_project_permission('project.members.manage',project_id)
    try:runtime.projects.remove_member(project_id,user_id)
    except WorkspaceError as exc:
        code=str(exc)
        if code=='PROJECT_MEMBER_NOT_FOUND':raise HTTPException(404,detail='Project member not found') from exc
        if code=='PROJECT_OWNER_REMOVE_FORBIDDEN':raise HTTPException(409,detail='The project owner cannot be removed') from exc
        raise HTTPException(400,detail=code) from exc
    return {'ok':True,'members':_public_project_members(project_id,runtime.projects.members(project_id))}
