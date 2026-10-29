from __future__ import annotations

"""Workspace-state and current-identity HTTP projections."""

from fastapi import APIRouter, Request

from backend import recovery_runtime
from backend.environment import runtime
from backend.request_context import _profile_request_user_id, _public_project_records
from . import profiles

router=APIRouter()

@router.get("/api/workspace")
def workspace_state(request:Request):
    user_id=_profile_request_user_id(request) or runtime.identity.active_profile_id()
    projects=runtime.projects.list(user_id=user_id)
    # An explicit request binding belongs to this browser tab. The registry/profile
    # target remains only the default for an unrouted tab or a fresh launch.
    routed=str(getattr(request.state,'breadmaker_project_id',None) or '')
    target=routed or runtime.projects.profile_project_target(user_id)
    if target and not any(str(item.get('id'))==str(target) for item in projects):target=None
    if not target and projects:target=str(projects[0]['id'])
    recovery_candidates=[] if runtime.sessions.requires_authentication else runtime.projects.recovery_candidates()
    native_root=str(runtime.native_projects.root) if runtime.capabilities.supports('native_filesystem') and runtime.native_projects is not None else None
    return {"projects":_public_project_records(projects),"active_project_id":target,"root":native_root,
            "actor":profiles._current_actor_public(target,require_membership=False,user_id=user_id),"runtime":runtime.mode,"capabilities":runtime.capabilities.public(),
            "recovery":recovery_runtime.current_recovery_mode(),"recovery_candidates":_public_project_records(recovery_candidates),"trash":_public_project_records(runtime.projects.list_trash(user_id=user_id))}

@router.get('/api/identity/current')
def current_identity():
    return profiles._current_actor_public(require_membership=False)
