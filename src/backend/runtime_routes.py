from __future__ import annotations

"""Application shell, locale, runtime/session, health, test-auth, and lease endpoints."""

import json
import os
import re
import tempfile
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from api.project_management import profiles
from auth import identity as identity_service
from backend import client_leases, portable_review_runtime, request_pipeline
from backend.runtime_contracts import TestAuthLoginIn
from backend.dependencies import feature_call
from backend.environment import APP_VERSION, LOCALES, STATIC, runtime
from backend.project_activation import activate_project as _activate_project
from workspace import workspace
from workspace.constants import APP_SCHEMA_VERSION
from workspace.models import WorkspaceError

router=APIRouter()

cleanup_temporary_portable_review = feature_call('reviews.portable','_cleanup_temporary_portable_review')

@router.get("/",response_class=HTMLResponse)
@router.get("/canvas",response_class=HTMLResponse)
@router.get("/maps",response_class=HTMLResponse)
@router.get("/tasks",response_class=HTMLResponse)
@router.get("/reviews",response_class=HTMLResponse)
@router.get("/p/{project_id}",response_class=HTMLResponse)
@router.get("/p/{project_id}/canvas",response_class=HTMLResponse)
@router.get("/p/{project_id}/maps",response_class=HTMLResponse)
@router.get("/p/{project_id}/tasks",response_class=HTMLResponse)
@router.get("/p/{project_id}/reviews",response_class=HTMLResponse)
def index(request:Request,project_id:str=''):
    if project_id and not re.fullmatch(r'[0-9a-f]{32}',str(project_id or '')):
        raise HTTPException(404,detail='PROJECT_NOT_FOUND',headers={'Cache-Control':'private, no-store'} if runtime.sessions.requires_authentication else None)
    if project_id and runtime.sessions.requires_authentication:
        if runtime.sessions.current() is None:
            raise HTTPException(401,detail='AUTHENTICATION_REQUIRED',headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'})
        try:
            runtime.identity.current_actor(project_id,require_membership=True)
        except (identity_service.AuthorizationError,WorkspaceError) as exc:
            raise HTTPException(404,detail='PROJECT_NOT_FOUND',headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'}) from exc
    source=(STATIC/"index.html").read_text(encoding="utf-8")
    if runtime.mode=='local':
        source=source.replace('<html lang="en">','<html lang="en" data-breadmaker-local>',1)
    return source

@router.get("/api/locales")
def list_locales():
    items=[]
    if LOCALES.exists():
        for path in sorted(LOCALES.glob("*.json")):
            try:
                payload=json.loads(path.read_text(encoding="utf-8"))
            except (OSError,json.JSONDecodeError):
                continue
            code=str(payload.get("code") or path.stem).strip()
            english_name=str(payload.get("english_name") or code).strip()
            native_name=str(payload.get("native_name") or english_name).strip()
            direction=str(payload.get("direction") or "ltr").strip().lower()
            if direction not in {"ltr","rtl"}: direction="ltr"
            items.append({"code":code,"english_name":english_name,"native_name":native_name,"direction":direction,"schema_version":int(payload.get("schema_version") or 1)})
    return {"locales":items,"default":"en"}

@router.get("/locales/{locale_code}.json")
def locale_file(locale_code: str):
    code=re.sub(r"[^A-Za-z0-9_-]","",str(locale_code or ""))
    path=LOCALES/f"{code}.json"
    if not code or not path.is_file(): raise HTTPException(404,"Locale not found")
    return FileResponse(path,media_type="application/json",headers={"Cache-Control":"no-store, max-age=0"})

@router.get("/api/runtime")
def runtime_state():
    # Kept as the canonical route for direct application dispatch/OpenAPI inspection.
    # Browser HTTP requests are answered by the project-free middleware fast path above.
    return request_pipeline._runtime_bootstrap_payload()

@router.get('/api/auth/session')
def auth_session_state():
    principal=runtime.sessions.current()
    if principal is None:return JSONResponse({'authenticated':False},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    profile=runtime.identity.current_profile(principal.user_id)
    return JSONResponse({'authenticated':True,'auth_method':principal.auth_method,'authenticated_at':principal.authenticated_at,'session_id':principal.session_id,'user':profiles._public_local_profile(profile)},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@router.post('/api/auth/logout')
def auth_logout(request:Request):
    token=request.cookies.get(runtime.sessions.cookie_name)
    # The session id is the ownership key for temporary BreadReviews; erase its
    # package and unlocked passphrase while that principal is still bound. An already
    # expired hosted session has no recoverable session id, so still clear the cookie
    # without turning logout back into an authenticated endpoint.
    if runtime.sessions.current() is not None or not runtime.sessions.requires_authentication:
        cleanup_temporary_portable_review()
    runtime.sessions.revoke(token)
    response=JSONResponse({'ok':True},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    response.delete_cookie(runtime.sessions.cookie_name,path='/',secure=runtime.sessions.cookie_secure,samesite='lax')
    return response

@router.get("/api/health")
def health():
    portable_active=portable_review_runtime.current_portable_review_path() is not None
    public_active=None if (portable_active or runtime.sessions.requires_authentication) else runtime.projects.active_id()
    return {"app":"Breadmaker Editor","version":APP_VERSION,"schema":APP_SCHEMA_VERSION,"ok":True,"instance":portable_review_runtime._PORTABLE_INSTANCE_ID,"runtime":runtime.mode,"active_project_id":public_active,"portable_review":portable_active}


_TEST_AUTH_FIXTURE_NAMES={
    'owner':'Breadmaker Test Owner',
    'editor':'Breadmaker Test Editor',
    'second_owner':'Breadmaker Test Second Owner',
    'unowned_user':'Breadmaker Test Unowned',
}
_TEST_AUTH_FIXTURE_LOCK=threading.Lock()
_TEST_AUTH_FIXTURE_INDEX=workspace.root/'test-auth-fixtures.json'

def _read_test_auth_fixture_index() -> dict[str,str]:
    try:
        raw=json.loads(_TEST_AUTH_FIXTURE_INDEX.read_text(encoding='utf-8'))
        if isinstance(raw,dict) and int(raw.get('version') or 0)==1 and isinstance(raw.get('fixtures'),dict):
            return {str(k):str(v) for k,v in raw['fixtures'].items() if k in _TEST_AUTH_FIXTURE_NAMES and str(v).startswith('usr_')}
    except (OSError,json.JSONDecodeError,ValueError,TypeError):pass
    return {}

def _write_test_auth_fixture_index(fixtures:dict[str,str]) -> None:
    _TEST_AUTH_FIXTURE_INDEX.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp_name=tempfile.mkstemp(prefix='test-auth-fixtures-',suffix='.json',dir=_TEST_AUTH_FIXTURE_INDEX.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as fh:
            json.dump({'version':1,'fixtures':fixtures},fh,sort_keys=True);fh.flush();os.fsync(fh.fileno())
        os.replace(tmp_name,_TEST_AUTH_FIXTURE_INDEX)
    finally:
        try:os.unlink(tmp_name)
        except FileNotFoundError:pass

def _test_auth_profile(fixture:str) -> dict:
    # Never identify privileged fixtures by a human-editable display name. A reused
    # test workspace may contain ordinary profiles with the same name. The test-only
    # registry binds fixture names to dedicated usr_ ids instead.
    fixtures=_read_test_auth_fixture_index();user_id=str(fixtures.get(fixture) or '')
    if user_id:
        try:return dict(workspace.local_profile(user_id))
        except WorkspaceError as exc:
            if str(exc)!='PROFILE_NOT_FOUND':raise
    profile=workspace.create_local_profile(_TEST_AUTH_FIXTURE_NAMES[fixture])
    fixtures[fixture]=str(profile['user_id']);_write_test_auth_fixture_index(fixtures)
    return profile

def _test_auth_owner_project() -> tuple[dict,dict]:
    profile=_test_auth_profile('owner');uid=str(profile['user_id'])
    for rec in workspace.list_projects(uid):
        member=workspace.project_membership(str(rec['id']),uid)
        if member and str(member.get('role'))=='owner':return profile,dict(rec)
    return profile,workspace.create_project('Breadmaker Test Project',activate=False,owner_user_id=uid)

def _prepare_test_auth_fixture(fixture:str) -> tuple[dict,str|None]:
    if fixture=='owner':
        profile,project=_test_auth_owner_project();return profile,str(project['id'])
    if fixture=='editor':
        _owner,project=_test_auth_owner_project();profile=_test_auth_profile('editor');uid=str(profile['user_id'])
        if workspace.project_membership(str(project['id']),uid) is None:workspace.add_project_member(str(project['id']),uid,'editor')
        return profile,str(project['id'])
    if fixture=='second_owner':
        profile=_test_auth_profile('second_owner');uid=str(profile['user_id'])
        owned=None
        for rec in workspace.list_projects(uid):
            member=workspace.project_membership(str(rec['id']),uid)
            if member and str(member.get('role'))=='owner':owned=dict(rec);break
        if owned is None:owned=workspace.create_project('Breadmaker Test Second Project',activate=False,owner_user_id=uid)
        return profile,str(owned['id'])
    profile=_test_auth_profile('unowned_user');uid=str(profile['user_id'])
    if workspace.list_projects(uid):
        # Tests are allowed to mutate membership. Preserve the semantic fixture contract
        # across runs by rotating to a fresh dedicated identity if it gained any access.
        profile=workspace.create_local_profile(_TEST_AUTH_FIXTURE_NAMES['unowned_user'])
        fixtures=_read_test_auth_fixture_index();fixtures['unowned_user']=str(profile['user_id']);_write_test_auth_fixture_index(fixtures)
    return profile,None

if runtime.sessions.test_bootstrap_enabled:
    @router.post('/__test__/login')
    def test_auth_login(payload:TestAuthLoginIn,request:Request):
        authorization=str(request.headers.get('authorization') or '')
        scheme,_,credential=authorization.partition(' ')
        if scheme.lower()!='bearer' or not runtime.sessions.verify_test_secret(credential):
            raise HTTPException(401,detail='TEST_AUTH_CREDENTIAL_INVALID')
        with _TEST_AUTH_FIXTURE_LOCK:
            profile,project_id=_prepare_test_auth_fixture(payload.fixture)
        token,principal=runtime.sessions.replace(request.cookies.get(runtime.sessions.cookie_name),str(profile['user_id']),auth_method='test_fixture')
        try:
            if project_id:
                with runtime.sessions.bind(token):_activate_project(project_id,user_id=str(profile['user_id']))
        except Exception:
            runtime.sessions.revoke(token);raise
        response=JSONResponse({'ok':True,'fixture':payload.fixture,'project_id':project_id,'user':profiles._public_local_profile({**profile,'kind':'account'})},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.set_cookie(
            runtime.sessions.cookie_name,token,max_age=runtime.sessions.max_age_seconds,path='/',
            secure=runtime.sessions.cookie_secure,httponly=True,samesite='lax',
        )
        return response

@router.post("/api/lease")
def client_lease(): return {"ok":True,"lease_seconds":client_leases._CLIENT_LEASE_SECONDS}

@router.post("/api/lease/release")
def release_client_lease(request: Request):
    client=(request.headers.get("x-breadmaker-client") or '').strip()[:120]
    project_id=str(getattr(request.state,'breadmaker_project_id',None) or '')
    if client: client_leases._release_client_lease(project_id,client)
    return {"ok":True}
