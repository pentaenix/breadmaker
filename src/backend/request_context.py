from __future__ import annotations

"""Request/project identity, authorization, and hosted quota helpers."""

from fastapi import HTTPException, Request

from auth import identity as identity_service
from backend.environment import runtime, _HOSTED_LIMITS

def _require_project_permission(permission:str,project_id:str|None=None):
    try:return runtime.authorization.require(permission,project_id)
    except identity_service.AuthorizationError as exc:
        raise HTTPException(403,detail='This profile does not have permission to perform that project action.') from exc

def _require_hosted_project_management(project_id:str|None=None):
    """Require Owner/project.manage for hosted project-administration surfaces.

    Local Breadmaker remains one trusted desktop installation. Hosted Editors may edit
    project content, but they must not reconfigure backups, mutate History retention,
    run repair/GC, or restore administrative backup state for the shared project.
    """
    if not runtime.sessions.requires_authentication:
        return None
    pid=str(project_id or _bound_project_id() or '')
    if not pid:
        raise HTTPException(404,detail='PROJECT_CONTEXT_REQUIRED')
    return _require_project_permission('project.manage',pid)

def _profile_request_user_id(request:Request) -> str | None:
    explicit=(request.headers.get('x-breadmaker-user') or request.query_params.get('user_id') or '').strip()[:128] or None
    try:return runtime.identity.request_user_id(explicit)
    except identity_service.AuthorizationError:raise HTTPException(403,detail='Identity does not match this Breadmaker session.')

def _public_project_record(record:dict|None) -> dict|None:
    if record is None:return None
    out=dict(record)
    if not runtime.capabilities.supports('native_filesystem'):
        out.pop('path',None)
    # A project's globally stable account owner id is installation metadata, not
    # collaboration content. Hosted clients use actor.role/member_id for authorization
    # and UI; exposing another user's usr_ id creates an unnecessary correlation key.
    if runtime.sessions.requires_authentication:
        out.pop('owner_user_id',None)
    return out

def _public_project_records(rows) -> list[dict]:
    return [item for item in (_public_project_record(dict(row)) for row in (rows or [])) if item is not None]

def _bound_project_id() -> str:
    context=runtime.context.current()
    if context is not None:return str(context.project_id or '')
    if runtime.sessions.requires_authentication:
        principal=runtime.sessions.current()
        if principal is None:return ''
        try:return str(runtime.projects.profile_project_target(principal.user_id) or '')
        except Exception:return ''
    return str(runtime.projects.active_id() or '')

def _bound_project() -> dict | None:
    pid=_bound_project_id()
    return runtime.projects.get(pid) if pid else None

def _project_is_current_for_bound_actor(project_id:str) -> bool:
    """Return whether project_id is the current target for this request's actor.

    Account sessions must never consult the process-global shell selection for
    rollback decisions. Local Breadmaker retains its existing one-shell semantics.
    """
    pid=str(project_id or '')
    if not pid:return False
    if runtime.sessions.requires_authentication:
        uid=_bound_user_id()
        if not uid:return False
        try:return str(runtime.projects.profile_project_target(uid) or '')==pid
        except Exception:return False
    return str(runtime.projects.active_id() or '')==pid

def _bound_user_id() -> str:
    context=runtime.context.current()
    if context is not None:return str(context.actor.user_id or '')
    try:return str(runtime.identity.active_profile_id() or '')
    except Exception:return ''

def _hosted_owner_storage_bytes(owner_user_id:str,*,stop_after:int|None=None) -> int:
    if _HOSTED_LIMITS is None:return 0
    return runtime.usage.owner_storage_bytes(str(owner_user_id or ''),stop_after=stop_after)

def _enforce_hosted_new_project_quota(owner_user_id:str,*,estimated_bytes:int=0) -> None:
    if _HOSTED_LIMITS is None:return
    uid=str(owner_user_id or '')
    if not uid:raise HTTPException(401,detail='AUTHENTICATION_REQUIRED')
    project_count=runtime.usage.owner_project_count(uid)
    if project_count>=_HOSTED_LIMITS.max_projects_per_owner:
        raise HTTPException(429,detail={'code':'HOSTED_PROJECT_QUOTA','message':'This account has reached its Breadmaker project quota.','params':{'max_projects':_HOSTED_LIMITS.max_projects_per_owner}})
    estimate=max(0,int(estimated_bytes or 0))
    if estimate>_HOSTED_LIMITS.max_project_bytes:
        raise HTTPException(413,detail={'code':'HOSTED_PROJECT_SIZE_QUOTA','message':'This project would exceed the hosted project storage quota.','params':{'max_bytes':_HOSTED_LIMITS.max_project_bytes}})
    current=_hosted_owner_storage_bytes(uid,stop_after=_HOSTED_LIMITS.max_owner_bytes)
    if current+estimate>_HOSTED_LIMITS.max_owner_bytes:
        raise HTTPException(413,detail={'code':'HOSTED_ACCOUNT_STORAGE_QUOTA','message':'This account has reached its Breadmaker storage quota.','params':{'max_bytes':_HOSTED_LIMITS.max_owner_bytes}})

def _enforce_hosted_project_storage(*,project_id:str|None=None,additional_bytes:int=0) -> None:
    if _HOSTED_LIMITS is None:return
    pid=str(project_id or _bound_project_id() or '')
    if not pid:return
    rec=runtime.projects.get(pid);extra=max(0,int(additional_bytes or 0))
    project_bytes=runtime.usage.project_storage_bytes(pid,stop_after=_HOSTED_LIMITS.max_project_bytes)
    if project_bytes+extra>_HOSTED_LIMITS.max_project_bytes:
        raise HTTPException(413,detail={'code':'HOSTED_PROJECT_SIZE_QUOTA','message':'This project has reached its hosted storage quota.','params':{'max_bytes':_HOSTED_LIMITS.max_project_bytes}})
    owner=str(rec.get('owner_user_id') or _bound_user_id() or '')
    owner_bytes=_hosted_owner_storage_bytes(owner,stop_after=_HOSTED_LIMITS.max_owner_bytes)
    if owner_bytes+extra>_HOSTED_LIMITS.max_owner_bytes:
        raise HTTPException(413,detail={'code':'HOSTED_ACCOUNT_STORAGE_QUOTA','message':'The project owner has reached the hosted Breadmaker storage quota.','params':{'max_bytes':_HOSTED_LIMITS.max_owner_bytes}})
