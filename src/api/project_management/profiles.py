from __future__ import annotations

"""Local/hosted profile HTTP surface and public profile projection."""

from urllib.parse import quote
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from auth import identity as identity_service
from api.project_management.contracts import LocalProfileCreate, LocalProfileIn
from backend.environment import runtime
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _profile_request_user_id, _public_project_record
from workspace.models import WorkspaceError

router=APIRouter()

def _current_actor_public(project_id:str|None=None,*,require_membership:bool=True,user_id:str|None=None) -> dict:
    try:return runtime.identity.current_actor(project_id,require_membership=require_membership,user_id=user_id).public()
    except identity_service.AuthorizationError:
        if require_membership:raise
        profile=runtime.identity.current_profile(user_id)
        return {'id':profile['user_id'],'user_id':profile['user_id'],'member_id':'','display_name':profile.get('display_name') or 'Writer','kind':profile.get('kind') or 'local','role':'','permissions':[]}

def _public_local_profile(profile:dict|None=None) -> dict:
    profile=dict(profile or runtime.identity.current_profile())
    revision=str(profile.get('avatar_revision') or '')
    user_id=str(profile['user_id'])
    return {
        'id':user_id,
        'display_name':profile['display_name'],
        'kind':str(profile.get('kind') or 'local'),
        'has_avatar':bool(profile.get('has_avatar')),
        'avatar_url':f'/api/profiles/{quote(user_id)}/avatar?v={quote(revision)}' if profile.get('has_avatar') else '',
        'created_at':profile.get('created_at') or '',
        'updated_at':profile.get('updated_at') or '',
    }


def _public_local_profiles() -> dict:
    active_id=runtime.identity.active_profile_id();profiles=[_public_local_profile(item) for item in runtime.identity.profiles()]
    active=next((item for item in profiles if item['id']==active_id),profiles[0] if profiles else None)
    return {'active_profile_id':active_id,'active':active,'profiles':profiles}




@router.get('/api/profile')
def get_local_profile(request:Request):
    user_id=_profile_request_user_id(request)
    return _public_local_profile(runtime.identity.current_profile(user_id) if user_id else None)

@router.get('/api/profiles')
def get_local_profiles():
    return _public_local_profiles()

@router.post('/api/profiles')
def create_local_profile(payload:LocalProfileCreate):
    try:profile=runtime.identity.create_profile(payload.display_name)
    except WorkspaceError as exc:
        if str(exc)=='PROFILE_NAME_REQUIRED':raise HTTPException(400,detail='Profile name is required') from exc
        raise
    except RuntimeError as exc:
        if str(exc)=='HOSTED_PROFILE_CREATION_DISABLED':raise HTTPException(403,detail='This hosted runtime uses one server identity until account authentication is connected.') from exc
        raise
    return {'profile':_public_local_profile(profile)}

@router.post('/api/profiles/{user_id}/activate')
def activate_local_profile(user_id:str):
    previous_profile=runtime.identity.active_profile_id()
    try:
        runtime.identity.activate_profile(user_id)
        target=runtime.projects.profile_project_target(user_id)
        if not target:target=runtime.projects.create('Untitled Book',activate=False,owner_user_id=user_id)['id']
        rec=_activate_project(target)
    except WorkspaceError as exc:
        try:runtime.identity.activate_profile(previous_profile)
        except Exception:pass
        if str(exc)=='PROFILE_NOT_FOUND':raise HTTPException(404,detail='Profile not found') from exc
        raise
    except RuntimeError as exc:
        try:runtime.identity.activate_profile(previous_profile)
        except Exception:pass
        if str(exc)=='HOSTED_PROFILE_SWITCH_DISABLED':raise HTTPException(403,detail='Profile switching is disabled in the hosted development runtime.') from exc
        raise
    except Exception:
        try:runtime.identity.activate_profile(previous_profile)
        except Exception:pass
        raise
    return {'profile':_public_local_profile(runtime.identity.current_profile()),'project':_public_project_record(rec),'reload':True}

@router.put('/api/profile')
def update_local_profile(payload:LocalProfileIn,request:Request):
    user_id=_profile_request_user_id(request)
    try:runtime.identity.update_profile(payload.display_name,user_id=user_id)
    except WorkspaceError as exc:
        if str(exc)=='PROFILE_NAME_REQUIRED':raise HTTPException(400,detail='Profile name is required') from exc
        raise
    return _public_local_profile(runtime.identity.current_profile(user_id) if user_id else None)

@router.get('/api/profiles/{user_id}/avatar')
def get_local_profile_avatar(user_id:str):
    try:profile=runtime.identity.current_profile(user_id)
    except WorkspaceError as exc:raise HTTPException(404,detail='Profile not found') from exc
    avatar=runtime.identity.avatar_bytes(user_id)
    if not profile.get('has_avatar') or not avatar:raise HTTPException(404,detail='Profile picture not found')
    return Response(content=avatar,media_type='image/webp',headers={'Cache-Control':'private, max-age=31536000, immutable','X-Content-Type-Options':'nosniff'})

@router.post('/api/profile/avatar')
async def update_local_profile_avatar(request:Request,file:UploadFile=File(...)):
    user_id=_profile_request_user_id(request)
    payload=await file.read(2*1024*1024+1)
    try:runtime.identity.save_avatar(payload,user_id=user_id)
    except WorkspaceError as exc:
        code=str(exc)
        if code=='PROFILE_AVATAR_SIZE':raise HTTPException(413,detail='Profile picture is too large') from exc
        if code=='PROFILE_AVATAR_FORMAT':raise HTTPException(400,detail='Profile picture must be a WebP image') from exc
        if code=='PROFILE_AVATAR_DIMENSIONS':raise HTTPException(400,detail='Profile picture must be at most 512 × 512 pixels') from exc
        raise
    return _public_local_profile(runtime.identity.current_profile(user_id) if user_id else None)

@router.delete('/api/profile/avatar')
def delete_local_profile_avatar(request:Request):
    user_id=_profile_request_user_id(request)
    runtime.identity.remove_avatar(user_id=user_id)
    return _public_local_profile(runtime.identity.current_profile(user_id) if user_id else None)
