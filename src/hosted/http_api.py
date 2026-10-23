from __future__ import annotations

"""Hosted-only HTTP surface for the Firestore/GCS runtime.

Local API modules intentionally remain untouched.  When BREADMAKER_RUNTIME is hosted,
``backend.core`` installs this router instead of the SQLite/project-directory product
routers.  Every canonical mutation below resolves the authenticated project member and
writes through ``HostedDomainStore`` or the specialized hosted project/asset ports.
"""

import json
import hashlib
import re
from contextlib import nullcontext
from typing import Any, Mapping
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from auth.identity import AuthorizationError
from auth.sessions import AuthenticationError
from backend.environment import APP_VERSION, LOCALES, STATIC, runtime, _HOSTED_LIMITS
from backend.configuration import _canvas_config_raw, _feature_flags_raw, _metro_view_config_raw
from hosted.firestore.contracts import DocumentAlreadyExists, DocumentConflict, DocumentNotFound, HostedAuthorizationError, HostedValidationError, ProjectUnavailable
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.packages import HostedProjectPackageService
from hosted.idempotency import bind_operations, operation_for, request_scope_key, stable_entity_id
from hosted.action_journal import bind_action
from security.http_request import canonical_request_path

platform_router=APIRouter()
product_router=APIRouter()

_PROJECT_PATH_RE=re.compile(r'^/api/projects/([0-9a-f]{32})(?:/|$)')
_SHELL_PROJECT_RE=re.compile(r'^/p/([0-9a-f]{32})(?:/|$)')
_PUBLIC_API={'/api/runtime','/api/health','/api/auth/session','/api/auth/logout','/api/auth/csrf','/api/auth/client-config','/api/auth/session/exchange','/api/features','/api/metro-view-config','/api/locales'}


def _projects():
    return runtime.projects


def _domain():
    domain=getattr(runtime.projects,'domain',None)
    if domain is None:raise RuntimeError('HOSTED_DOMAIN_STORE_REQUIRED')
    return domain


def _documents():
    value=getattr(runtime.projects,'documents',None)
    if value is None:raise RuntimeError('HOSTED_DOCUMENT_STORE_REQUIRED')
    return value


def _uid() -> str:
    return require_user_id(runtime.identity.active_profile_id())


def _pid(request: Request | None = None) -> str:
    current=runtime.context.current()
    if current is not None and current.project_id:return require_project_id(current.project_id)
    if request is not None:
        explicit=str(request.headers.get('x-breadmaker-project') or request.query_params.get('project_id') or '')
        if explicit:return require_project_id(explicit)
    target=runtime.projects.profile_project_target(_uid())
    if not target:raise HTTPException(404,detail='PROJECT_CONTEXT_REQUIRED')
    return require_project_id(target)


def _json_public(value: Any) -> Any:
    if isinstance(value,dict):return {str(k):_json_public(v) for k,v in value.items() if not str(k).startswith('_bm_')}
    if isinstance(value,list):return [_json_public(v) for v in value]
    return value


def _error(exc: Exception) -> HTTPException:
    code=str(exc) or type(exc).__name__
    if isinstance(exc,(DocumentNotFound,ProjectUnavailable)):return HTTPException(404,detail='PROJECT_NOT_FOUND' if isinstance(exc,ProjectUnavailable) else code)
    if isinstance(exc,(HostedAuthorizationError,AuthorizationError)):return HTTPException(403,detail='PROJECT_PERMISSION_DENIED')
    if isinstance(exc,(DocumentConflict,DocumentAlreadyExists)):return HTTPException(409,detail=code)
    if isinstance(exc,(HostedValidationError,ValueError)):return HTTPException(400,detail=code)
    if isinstance(exc,FileNotFoundError):return HTTPException(404,detail='ASSET_NOT_FOUND')
    return HTTPException(500,detail='HOSTED_OPERATION_FAILED')


async def hosted_request_boundary(request: Request, call_next):
    path=canonical_request_path(request)
    # These installed application files are already anonymously readable. Do
    # not perform synchronous provider/Firestore session reads for every CSS/JS
    # file: they serialize concurrent startup downloads on the ASGI event loop.
    # Private assets remain under /api/assets and MUST use the full boundary.
    if request.method.upper() in {'GET','HEAD'} and (
            path.startswith('/static/') or re.fullmatch(r'/locales/[A-Za-z0-9_-]+\.json',path)):
        return await call_next(request)
    if request.method.upper() in {'GET','HEAD'} and path=='/api/runtime':
        return JSONResponse({
            'mode':runtime.mode,'capabilities':runtime.capabilities.public(),'metadata':runtime.capabilities.metadata(),
            'auth':runtime.sessions.metadata(),'project_header':'X-Breadmaker-Project','actor_header':None if runtime.sessions.requires_authentication else 'X-Breadmaker-User',
        },headers={'Cache-Control':'no-store'})
    mutating=path.startswith('/api/') and request.method.upper() not in {'GET','HEAD','OPTIONS'}
    if mutating:
        origin=str(request.headers.get('origin') or '').strip()
        if runtime.sessions.requires_authentication and not origin:
            return JSONResponse({'detail':'Breadmaker hosted authenticated changes require an Origin header.'},status_code=403)
        if origin:
            configured=str(__import__('os').environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip().rstrip('/')
            expected=urlparse(configured) if configured else urlparse(str(request.base_url).rstrip('/'))
            supplied=urlparse(origin)
            if (supplied.scheme.lower(),supplied.netloc.lower())!=(expected.scheme.lower(),expected.netloc.lower()):
                return JSONResponse({'detail':'Breadmaker hosted requests must come from the same origin.'},status_code=403)
    token=request.cookies.get(runtime.sessions.cookie_name)
    try:
        with runtime.sessions.bind(token):
            if path.startswith('/api/') and path not in _PUBLIC_API and runtime.sessions.requires_authentication and runtime.sessions.current() is None:
                return JSONResponse({'detail':'AUTHENTICATION_REQUIRED'},status_code=401,headers={'Cache-Control':'private, no-store'})
            project_id=''
            match=_PROJECT_PATH_RE.match(path)
            if match:project_id=match.group(1)
            if not project_id and path.startswith('/api/') and path not in _PUBLIC_API and path not in {'/api/workspace','/api/profile','/api/profiles','/api/projects','/api/projects/import','/api/data/export','/api/identity/current'}:
                project_id=str(request.headers.get('x-breadmaker-project') or request.query_params.get('project_id') or '')
                if not project_id:
                    try:project_id=str(runtime.projects.profile_project_target(runtime.identity.active_profile_id()) or '')
                    except Exception:project_id=''
            user_id=''
            if not runtime.sessions.requires_authentication or runtime.sessions.current() is not None:
                user_id=runtime.identity.request_user_id(None)
            request.state.breadmaker_project_id=project_id or None
            request.state.breadmaker_user_id=user_id or None
            # A retry key is scoped to this authenticated actor + target URL. It
            # generates stable Firestore transaction IDs; no in-process reply cache.
            # Duplicate headers are rejected rather than arbitrarily choosing one.
            idempotency_headers=[value for name,value in request.scope.get('headers',[])
                if bytes(name).lower()==b'idempotency-key']
            scope_key=None
            if mutating and idempotency_headers:
                if len(idempotency_headers)!=1 or not user_id:
                    return JSONResponse({'detail':'HOSTED_IDEMPOTENCY_KEY_INVALID'},status_code=400)
                try:
                    raw_key=bytes(idempotency_headers[0]).decode('ascii')
                    scope_key=request_scope_key(raw_key,actor=user_id,method=request.method,path=path+("?"+request.url.query if request.url.query else ""))
                except (UnicodeError,ValueError):
                    return JSONResponse({'detail':'HOSTED_IDEMPOTENCY_KEY_INVALID'},status_code=400)
            cm=runtime.context.bind(project_id,user_id=user_id or None) if project_id else nullcontext(None)
            action_headers=request.headers.getlist('x-breadmaker-action')
            if len(action_headers)>1:
                return JSONResponse({'detail':'HOSTED_ACTION_HEADER_INVALID'},status_code=400)
            action_token=action_headers[0] if action_headers and mutating and not path.startswith('/api/undo/') else None
            with cm, bind_operations(scope_key), bind_action(action_token,project_id,user_id):
                response=await call_next(request)
                if runtime.sessions.requires_authentication and path.startswith('/api/'):
                    response.headers['Cache-Control']='private, no-store'
                return response
    except AuthenticationError:
        return JSONResponse({'detail':'AUTHENTICATION_REQUIRED'},status_code=401)
    except (AuthorizationError,ProjectUnavailable,HostedAuthorizationError):
        return JSONResponse({'detail':'PROJECT_NOT_FOUND'},status_code=404,headers={'Cache-Control':'private, no-store'})
    except HostedValidationError as exc:
        return JSONResponse({'detail':str(exc)},status_code=400)
    except DocumentNotFound:
        return JSONResponse({'detail':'UNDO_TOKEN_NOT_FOUND'},status_code=404)
    except DocumentConflict as exc:
        return JSONResponse({'detail':str(exc)},status_code=409)


def install_request_boundary(app: FastAPI) -> None:
    app.middleware('http')(hosted_request_boundary)


# ---------------------------------------------------------------------------
# Shell / platform
# ---------------------------------------------------------------------------

@platform_router.get('/',response_class=HTMLResponse)
@platform_router.get('/canvas',response_class=HTMLResponse)
@platform_router.get('/maps',response_class=HTMLResponse)
@platform_router.get('/tasks',response_class=HTMLResponse)
@platform_router.get('/reviews',response_class=HTMLResponse)
@platform_router.get('/p/{project_id}',response_class=HTMLResponse)
@platform_router.get('/p/{project_id}/canvas',response_class=HTMLResponse)
@platform_router.get('/p/{project_id}/maps',response_class=HTMLResponse)
@platform_router.get('/p/{project_id}/tasks',response_class=HTMLResponse)
@platform_router.get('/p/{project_id}/reviews',response_class=HTMLResponse)
def hosted_shell(project_id: str=''):
    if runtime.sessions.requires_authentication and runtime.sessions.current() is None:
        from urllib.parse import quote
        target='/' if not project_id else f'/p/{project_id}'
        return RedirectResponse(f'/auth/login?return_to={quote(target,safe="/")}',status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    if project_id:
        try:runtime.identity.current_actor(require_project_id(project_id),require_membership=True)
        except Exception as exc:raise HTTPException(404,detail='PROJECT_NOT_FOUND') from exc
    return (STATIC/'index.html').read_text(encoding='utf-8')


@platform_router.get('/api/health')
def hosted_health():
    return {'ok':True,'runtime':runtime.mode,'persistence':'firestore_gcs','version':APP_VERSION}


@platform_router.get('/api/locales')
def hosted_locales():
    items=[]
    if LOCALES.exists():
        for path in sorted(LOCALES.glob('*.json')):
            try:payload=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,json.JSONDecodeError):continue
            code=str(payload.get('code') or path.stem).strip()
            english=str(payload.get('english_name') or code).strip()
            native=str(payload.get('native_name') or english).strip()
            direction=str(payload.get('direction') or 'ltr').strip().lower()
            if direction not in {'ltr','rtl'}:direction='ltr'
            items.append({'code':code,'english_name':english,'native_name':native,'direction':direction,'schema_version':int(payload.get('schema_version') or 1)})
    return {'locales':items,'default':'en'}


@platform_router.get('/locales/{locale_code}.json')
def hosted_locale_file(locale_code:str):
    code=re.sub(r'[^A-Za-z0-9_-]','',str(locale_code or ''))
    path=LOCALES/f'{code}.json'
    if not code or not path.is_file():raise HTTPException(404,detail='Locale not found')
    return FileResponse(path,media_type='application/json',headers={'Cache-Control':'no-store, max-age=0'})


@platform_router.get('/api/auth/session')
def hosted_auth_session():
    principal=runtime.sessions.current()
    if principal is None:
        return JSONResponse({'authenticated':False},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    return JSONResponse({
        'authenticated':True,'auth_method':principal.auth_method,'authenticated_at':principal.authenticated_at,
        'session_id':principal.session_id,'user':runtime.identity.current_profile(principal.user_id),
    },headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@platform_router.post('/api/auth/logout')
def hosted_auth_logout(request:Request):
    token=request.cookies.get(runtime.sessions.cookie_name)
    runtime.sessions.revoke(token)
    response=JSONResponse({'ok':True},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    response.delete_cookie(runtime.sessions.cookie_name,path='/',secure=runtime.sessions.cookie_secure,samesite='lax')
    return response


@platform_router.post('/api/lease')
def hosted_editor_lease():
    # Cloud concurrency is protected by Firestore generation CAS, not a process-local
    # single-tab lock. Keep the legacy bootstrap endpoint as an explicit no-op contract.
    return {'ok':True,'mode':'hosted_multiwriter','exclusive':False}


@platform_router.post('/api/lease/release')
def hosted_editor_lease_release():
    return {'ok':True,'mode':'hosted_multiwriter','exclusive':False}


@platform_router.get('/api/workspace')
def hosted_workspace():
    uid=_uid();projects=runtime.projects.list(uid);target=runtime.projects.profile_project_target(uid)
    return {
        'projects':[_json_public(row) for row in projects],
        'active_project_id':target,'root':None,'actor':runtime.identity.current_actor(target,require_membership=False).public() if target else runtime.identity.current_actor(require_membership=False).public(),
        'runtime':runtime.mode,'capabilities':runtime.capabilities.public(),'recovery':None,'recovery_candidates':[],'trash':runtime.projects.list_trash(uid),
    }


@platform_router.get('/api/identity/current')
def hosted_identity_current():return runtime.identity.current_actor(require_membership=False).public()

@platform_router.get('/api/profile')
def hosted_profile():return runtime.identity.current_profile()

@platform_router.get('/api/profiles')
def hosted_profiles():return {'profiles':runtime.identity.profiles(),'active_profile_id':runtime.identity.active_profile_id()}

@platform_router.put('/api/profile')
async def hosted_profile_update(request:Request):
    body=await request.json();return runtime.identity.update_profile(str(body.get('display_name') or body.get('name') or 'Writer'))

@platform_router.get('/api/profiles/{user_id}/avatar')
def hosted_avatar(user_id:str):
    try:data=runtime.identity.avatar_bytes(user_id)
    except Exception as exc:raise _error(exc)
    if data is None:raise HTTPException(404,detail='AVATAR_NOT_FOUND')
    return Response(data,media_type='image/webp',headers={'Cache-Control':'private, no-store'})

@platform_router.post('/api/profile/avatar')
async def hosted_avatar_save(file:UploadFile=File(...)):
    return runtime.identity.save_avatar(await _bounded_upload(file,max_bytes=5*1024*1024))

@platform_router.delete('/api/profile/avatar')
def hosted_avatar_delete():return runtime.identity.remove_avatar()


@platform_router.post('/api/projects')
async def hosted_project_create(request:Request):
    body=await request.json()
    try:
        op=operation_for('project_create')
        return {'project':runtime.projects.create(str(body.get('name') or body.get('title') or 'Untitled Book'),activate=True,idempotency_operation=op),'reload':True}
    except Exception as exc:raise _error(exc)

@platform_router.post('/api/projects/{project_id}/activate')
def hosted_project_activate(project_id:str):
    try:return {'project':runtime.projects.activate(project_id),'reload':True}
    except Exception as exc:raise _error(exc)

@platform_router.patch('/api/projects/{project_id}')
async def hosted_project_rename(project_id:str,request:Request):
    body=await request.json()
    try:return runtime.projects.rename(project_id,str(body.get('name') or body.get('title') or 'Untitled'))
    except Exception as exc:raise _error(exc)

@platform_router.delete('/api/projects/{project_id}')
def hosted_project_delete(project_id:str):
    try:return {'project':runtime.projects.delete(project_id),'reload':True}
    except Exception as exc:raise _error(exc)

@platform_router.get('/api/projects/{project_id}/members')
def hosted_project_members(project_id:str):
    try:return {'members':runtime.projects.members(project_id)}
    except Exception as exc:raise _error(exc)

@platform_router.post('/api/projects/{project_id}/members')
async def hosted_project_add_member(project_id:str,request:Request):
    body=await request.json()
    try:return runtime.projects.add_member(project_id,str(body.get('user_id') or ''),str(body.get('role') or 'editor'))
    except Exception as exc:raise _error(exc)

@platform_router.delete('/api/projects/{project_id}/members/{user_id}')
def hosted_project_remove_member(project_id:str,user_id:str):
    try:runtime.projects.remove_member(project_id,user_id);return {'ok':True}
    except Exception as exc:raise _error(exc)

@platform_router.post('/api/trash/{trash_id}/restore')
def hosted_restore_project(trash_id:str):
    try:return {'project':runtime.projects.restore_from_trash(trash_id),'reload':True}
    except Exception as exc:raise _error(exc)

@platform_router.get('/api/projects/{project_id}/export')
def hosted_project_export(project_id:str):
    try:
        payload=HostedProjectPackageService(runtime.projects).export_local_bytes(require_project_id(project_id),_uid())
    except Exception as exc:raise _error(exc)
    return Response(payload,media_type='application/zip',headers={'Content-Disposition':'attachment; filename=project.breadmaker','Cache-Control':'private, no-store'})

@platform_router.post('/api/projects/import')
async def hosted_project_import(file:UploadFile=File(...),name:str=''):
    try:
        # Inline package decoding has a bounded memory budget. Larger durable
        # transfers require the M9/M10 direct-job/object-store protocol.
        budget=min(64*1024*1024,_HOSTED_LIMITS.project_package_bytes if _HOSTED_LIMITS else 64*1024*1024)
        payload=await _bounded_upload(file,max_bytes=budget)
        project=HostedProjectPackageService(runtime.projects).import_bytes(payload,_uid(),name_override=name or None,operation=operation_for('project_import'))
    except Exception as exc:raise _error(exc)
    return {'project':project,'reload':True}

@platform_router.get('/api/data/export')
def hosted_data_export():
    target=runtime.projects.profile_project_target(_uid())
    if not target:raise HTTPException(404,detail='PROJECT_CONTEXT_REQUIRED')
    payload=HostedProjectPackageService(runtime.projects).export_local_bytes(target,_uid())
    return Response(payload,media_type='application/zip',headers={'Content-Disposition':'attachment; filename=project.breadmaker','Cache-Control':'private, no-store'})

@platform_router.get('/api/project/metadata')
def hosted_project_metadata(request:Request):
    pid=_pid(request);project=runtime.projects.get(pid);return {'project':project,'generation':project.get('generation'),'runtime':runtime.mode}

@platform_router.get('/api/project/health')
def hosted_project_health(request:Request):
    pid=_pid(request);runtime.projects.get(pid)
    return {'ok':True,'database':{'ok':True,'kind':'firestore','sqlite':False},'objects':{'ok':True,'kind':'gcs'},'project_id':pid}

@platform_router.post('/api/assets')
async def hosted_asset_upload(file:UploadFile=File(...)):
    raw=await _bounded_upload(file,max_bytes=12*1024*1024);name=str(file.filename or '').lower();ext='.'+name.rsplit('.',1)[1] if '.' in name else ''
    try:return runtime.assets.put_blob(raw,ext).__dict__
    except Exception as exc:raise _error(exc)

@platform_router.get('/api/assets/{asset_path:path}')
def hosted_asset_read(asset_path:str):
    try:data=runtime.assets.read_blob(asset_path)
    except Exception as exc:raise _error(exc)
    ext=asset_path.rsplit('.',1)[-1].lower();media={'png':'image/png','jpg':'image/jpeg','webp':'image/webp'}.get(ext,'application/octet-stream')
    return Response(data,media_type=media,headers={'Cache-Control':'private, max-age=31536000, immutable'})


@platform_router.get('/api/integrity/deep')
def hosted_integrity_deep(request:Request):
    pid=_pid(request);project=runtime.projects.get(pid)
    problems=[]
    for asset in _domain().list('project_asset',{'project_id':pid},user_id=_uid()):
        meta=runtime.projects.objects.stat(str(asset.get('object_name') or ''))
        if meta is None or int(meta.generation)!=int(asset.get('object_generation') or -1) or str(meta.sha256)!=str(asset.get('sha256') or ''):
            problems.append({'kind':'asset_pointer','asset_id':asset.get('id')})
    return {'ok':not problems,'database':{'ok':True,'kind':'firestore'},'objects':{'ok':not problems,'kind':'gcs','problems':problems},'disk':None,'project_id':pid,'generation':project.get('generation')}


# ---------------------------------------------------------------------------
# Generic entity helpers
# ---------------------------------------------------------------------------

async def _body(request:Request) -> dict[str,Any]:
    try:value=await request.json()
    except Exception:raise HTTPException(400,detail='HOSTED_JSON_INVALID')
    if not isinstance(value,dict):raise HTTPException(400,detail='HOSTED_JSON_OBJECT_REQUIRED')
    return value


async def _bounded_upload(file:UploadFile, *, max_bytes:int) -> bytes:
    """Read at most the allowed bytes even when bypassing outer ASGI middleware.

    File handlers are also called directly by conformance fixtures. Multipart
    Content-Length cannot be trusted as an enforcement boundary, and Cloud Run
    memory is not a durable upload staging mechanism.
    """
    chunks=[];size=0
    while True:
        chunk=await file.read(min(1024*1024,max_bytes-size+1))
        if not chunk:break
        size+=len(chunk)
        if size>max_bytes:raise HTTPException(413,detail='HOSTED_UPLOAD_TOO_LARGE')
        chunks.append(bytes(chunk))
    return b''.join(chunks)


def _create(entity:str,parents:Mapping[str,str],payload:Mapping[str,Any],entity_id:str|None=None):
    try:
        op=operation_for('entity_create:'+entity)
        if op is not None and entity_id is None:
            from hosted.domain_store import _ID_FIELDS
            entity_id=stable_entity_id(_ID_FIELDS[entity][1],op)
        return _json_public(_domain().create(entity,parents,payload,user_id=_uid(),entity_id=entity_id,operation=op))
    except Exception as exc:raise _error(exc)

def _list(entity:str,parents:Mapping[str,str],**filters):
    try:return _json_public(_domain().list(entity,parents,user_id=_uid(),**filters))
    except Exception as exc:raise _error(exc)

def _get(entity:str,ids:Mapping[str,str]):
    try:return _json_public(_domain().get(entity,ids,user_id=_uid()))
    except Exception as exc:raise _error(exc)

def _update(entity:str,ids:Mapping[str,str],patch:Mapping[str,Any]):
    try:return _json_public(_domain().update(entity,ids,patch,user_id=_uid(),expected_generation=patch.get('generation') if isinstance(patch,dict) else None,operation=operation_for('entity_update:'+entity)))
    except Exception as exc:raise _error(exc)

def _delete(entity:str,ids:Mapping[str,str]):
    try:_domain().delete(entity,ids,user_id=_uid());return {'ok':True}
    except Exception as exc:raise _error(exc)


def _project_snapshot(pid:str, *, manuscript_only:bool=False, navigation_only:bool=False, manifest_only:bool=False) -> dict[str,Any]:
    uid=_uid();domain=_domain();parents={'project_id':pid}
    begin_generation=int(runtime.projects.get(pid).get('generation') or 0)
    tabs=domain.list('document_tab',parents,user_id=uid)
    chapters=domain.list('chapter',parents,user_id=uid)
    scenes=domain.list('scene',parents,user_id=uid)
    navigation=domain.manuscript_navigation(pid,user_id=uid) if navigation_only else {}
    blocks=[] if navigation_only or manifest_only else domain.list('manuscript_block',parents,user_id=uid)
    if manuscript_only:
        final_project=runtime.projects.get(pid)
        if int(final_project.get('generation') or 0)!=begin_generation:
            raise DocumentConflict('HOSTED_SNAPSHOT_GENERATION_CHANGED')
        # Full Text needs only manuscript entities. Keep each authorized read,
        # immutable payload verification, and the original generation fence.
        return {'project':final_project,'tabs':tabs,'documents':tabs,
            'chapters':[{**chapter,'revision':str(begin_generation)} for chapter in chapters],
            'scenes':scenes,'blocks':blocks,'scene_gaps':[]}
    labels=domain.list('markup_label',parents,user_id=uid)
    icon_markers=domain.list('icon_marker',parents,user_id=uid)
    canvases=domain.list('canvas',parents,user_id=uid)
    maps=domain.list('story_map',parents,user_id=uid)
    reviews=domain.list('review',parents,user_id=uid)
    bookmark=runtime.projects.member_state(pid,user_id=uid)
    chapter_outlines=navigation.get('chapter_outlines') if navigation_only else [{k:row.get(k) for k in ('id','chapter_id','position','semantic_style','text_content')} for row in blocks if str(row.get('semantic_style') or '') in {'heading2','heading3','heading4'}]
    final_project=runtime.projects.get(pid)
    if int(final_project.get('generation') or 0)!=begin_generation:
        raise DocumentConflict('HOSTED_SNAPSHOT_GENERATION_CHANGED')
    # The browser must carry the same project revision used by atomic chapter
    # replacement. Missing revisions previously became an invalid empty string.
    chapters=[{**chapter,'revision':str(begin_generation)} for chapter in chapters]
    return {
        'project':final_project,'tabs':tabs,'documents':tabs,'chapters':chapters,'chapter_outlines':chapter_outlines,
        'canvases':canvases,'scenes':scenes,'scene_gaps':[],'bookmark':bookmark,'labels':labels,'icon_markers':icon_markers,
        'maps':maps,'reviews':reviews,'features':_feature_flags_raw()['features'],'metro_view_config':_metro_view_config_raw(),
        'canvas_config':_canvas_config_raw(),'blocks':blocks,'manuscript_navigation':navigation,
    }


@product_router.get('/api/bootstrap')
def hosted_bootstrap(request:Request):
    try:return _project_snapshot(_pid(request),navigation_only=request.query_params.get('navigation_only')=='true')
    except Exception as exc:raise _error(exc)

@product_router.get('/api/book')
def hosted_book(request:Request):
    def tag(pid,tab,generation):
        material=json.dumps([pid,tab,int(generation)],separators=(',',':')).encode()
        return '"bm-'+hashlib.sha256(material).hexdigest()+'"'
    try:
        pid=_pid(request);tab=str(request.query_params.get('tab_id') or '')
        conditional=request.headers.get('if-none-match')
        if conditional:
            # This explicitly checks CURRENT membership before considering 304.
            # Reuse means the SAME revision; never rebase old text to a newer one.
            generation=_domain().project_generation(pid,_uid())
            if conditional==tag(pid,tab,generation):
                return Response(status_code=304,headers={'Cache-Control':'private, no-store','ETag':conditional})
        manifest=request.query_params.get('manifest')=='true'
        data=_project_snapshot(pid,manuscript_only=True,manifest_only=manifest)
        if manifest:
            if tab and not any(str(row['id'])==tab for row in data['tabs']):
                raise DocumentNotFound('DOCUMENT_NOT_FOUND')
            data['chapters']=[row for row in data['chapters'] if not tab or str(row.get('tab_id'))==tab]
        data['read_tag']=tag(pid,tab,data['project']['generation'])
        return JSONResponse(data,headers={'Cache-Control':'private, no-store','ETag':data['read_tag']})
    except Exception as exc:raise _error(exc)

# Tabs/documents
@product_router.post('/api/documents')
async def hosted_document_create(request:Request):return _create('document_tab',{'project_id':_pid(request)},await _body(request))
@product_router.patch('/api/documents/{did}')
async def hosted_document_update(did:str,request:Request):return _update('document_tab',{'project_id':_pid(request),'tab_id':did},await _body(request))
@product_router.delete('/api/documents/{did}')
def hosted_document_delete(did:str,request:Request):return _delete('document_tab',{'project_id':_pid(request),'tab_id':did})

# Chapters / manuscript blocks
@product_router.post('/api/chapters')
async def hosted_chapter_create(request:Request):return _create('chapter',{'project_id':_pid(request)},await _body(request))
@product_router.get('/api/chapters/{cid}')
def hosted_chapter_get(cid:str,request:Request):
    pid=_pid(request)
    generation=_domain().project_generation(pid,_uid())
    chapter=_get('chapter',{'project_id':pid,'chapter_id':cid})
    page={}
    if 'block_offset' in request.query_params:
        try:
            offset=int(request.query_params['block_offset'])
            if offset<0:raise ValueError()
        except ValueError:raise HTTPException(400,detail='HOSTED_MANUSCRIPT_PAGE_INVALID')
        page={'page_offset':offset,'page_size':128}
    blocks=_list('manuscript_block',{'project_id':pid},field='chapter_id',value=cid,**page)
    if _domain().project_generation(pid,_uid())!=generation:
        raise HTTPException(409,detail='HOSTED_SNAPSHOT_GENERATION_CHANGED')
    revision=str(generation)
    return {'chapter':{**chapter,'revision':revision},'blocks':blocks,'revision':revision,
        'next_block_offset':page['page_offset']+len(blocks) if page and len(blocks)==128 else None}
@product_router.patch('/api/chapters/{cid}')
@product_router.patch('/api/chapters/{cid}/title')
async def hosted_chapter_update(cid:str,request:Request):
    pid=_pid(request);patch=dict(await _body(request))
    raw=patch.pop('expected_revision',None)
    try:
        revision=_domain().project_generation(pid,_uid()) if raw is None else int(raw)
        if revision<1:raise ValueError('invalid revision')
        return _json_public(_domain().update('chapter',{'project_id':pid,'chapter_id':cid},patch,
            user_id=_uid(),expected_generation=patch.get('generation'),
            operation=operation_for('chapter_update'),expected_project_revision=revision))
    except Exception as exc:raise _error(exc)
@product_router.delete('/api/chapters/{cid}')
def hosted_chapter_delete(cid:str,request:Request):return _delete('chapter',{'project_id':_pid(request),'chapter_id':cid})
@product_router.put('/api/chapters/{cid}/blocks')
async def hosted_chapter_blocks(cid:str,request:Request):
    from hosted.chapter_mutations import replace_chapter_blocks
    body=await _body(request)
    rows=body.get('blocks')
    if not isinstance(rows,list):raise HTTPException(400,detail='HOSTED_CHAPTER_BLOCKS_REQUIRED')
    try:
        return replace_chapter_blocks(_domain(),project_id=_pid(request),chapter_id=cid,user_id=_uid(),
            blocks=rows,expected_revision=body.get('expected_revision'),operation=operation_for('chapter_replace'))
    except Exception as exc:raise _error(exc)

# Scenes
@product_router.post('/api/scenes')
async def hosted_scene_create(request:Request):return _create('scene',{'project_id':_pid(request)},await _body(request))
@product_router.patch('/api/scenes/{sid}')
async def hosted_scene_update(sid:str,request:Request):return _update('scene',{'project_id':_pid(request),'scene_id':sid},await _body(request))
@product_router.delete('/api/scenes/{sid}')
def hosted_scene_delete(sid:str,request:Request):return _delete('scene',{'project_id':_pid(request),'scene_id':sid})
@product_router.get('/api/scenes/{sid}/manuscript')
def hosted_scene_manuscript(sid:str,request:Request):
    pid=_pid(request);scene=_get('scene',{'project_id':pid,'scene_id':sid});blocks=_list('manuscript_block',{'project_id':pid},field='scene_id',value=sid);return {'scene':scene,'blocks':blocks}

# Labels / annotations
@product_router.get('/api/annotations')
def hosted_annotations(request:Request):return {'annotations':_list('annotation',{'project_id':_pid(request)})}
@product_router.post('/api/annotations/batch')
async def hosted_annotations_batch(request:Request):
    pid=_pid(request);body=await _body(request)
    rows=body.get('annotations') if isinstance(body.get('annotations'),list) else body.get('items') if isinstance(body.get('items'),list) else []
    payloads=[dict(row) for row in rows if isinstance(row,dict)]
    try:
        created=_domain().create_batch_atomic('annotation',{'project_id':pid},payloads,user_id=_uid(),operation=operation_for('annotation_batch'))
    except Exception as exc:raise _error(exc)
    return {'annotations':_json_public(created),'ids':[row.get('id') for row in created]}
@product_router.patch('/api/annotations/{aid}')
async def hosted_annotation_update(aid:str,request:Request):return _update('annotation',{'project_id':_pid(request),'annotation_id':aid},await _body(request))
@product_router.post('/api/labels')
async def hosted_label_create(request:Request):return _create('markup_label',{'project_id':_pid(request)},await _body(request))

# Comments
@product_router.get('/api/comments')
def hosted_comments(request:Request):return {'threads':_list('comment_thread',{'project_id':_pid(request)})}
@product_router.post('/api/comments')
async def hosted_comment_create(request:Request):return _create('comment_thread',{'project_id':_pid(request)},await _body(request))
@product_router.patch('/api/comments/{tid}')
async def hosted_comment_update(tid:str,request:Request):return _update('comment_thread',{'project_id':_pid(request),'thread_id':tid},await _body(request))
@product_router.delete('/api/comments/{tid}')
def hosted_comment_delete(tid:str,request:Request):return _delete('comment_thread',{'project_id':_pid(request),'thread_id':tid})
@product_router.post('/api/comments/{tid}/messages')
async def hosted_comment_message(tid:str,request:Request):return _create('comment_message',{'project_id':_pid(request),'thread_id':tid},await _body(request))

# Per-member bookmark / UI state

@product_router.get('/api/bookmark')
def hosted_bookmark_get(request:Request):
    pid=_pid(request);row=runtime.projects.member_state(pid,user_id=_uid());return row or {'bookmark':None}
@product_router.put('/api/bookmark')
async def hosted_bookmark_put(request:Request):
    pid=_pid(request);return runtime.projects.put_member_state(pid,await _body(request),user_id=_uid())
@product_router.delete('/api/bookmark')
def hosted_bookmark_delete(request:Request):
    pid=_pid(request);runtime.projects.delete_member_state(pid,user_id=_uid());return {'ok':True}
@product_router.get('/api/ui-state')
def hosted_ui_state_batch(request:Request,keys:str=''):
    pid=_pid(request);uid=_uid();values={}
    for raw in str(keys or '').split(','):
        key=raw.strip()
        if not key:continue
        try:key=require_opaque_id(key,label='key')
        except Exception:continue
        row=runtime.projects.member_state(pid,key=key,user_id=uid);values[key]=None if row is None else row.get('value',row)
    return {'values':values}

@product_router.get('/api/ui-state/{key}')
def hosted_ui_state_get(key:str,request:Request):
    key=require_opaque_id(key,label='key');return runtime.projects.member_state(_pid(request),key=key,user_id=_uid()) or {'key':key,'value':None}
@product_router.put('/api/ui-state/{key}')
async def hosted_ui_state_put(key:str,request:Request):
    key=require_opaque_id(key,label='key');return runtime.projects.put_member_state(_pid(request),await _body(request),key=key,user_id=_uid())

# Tasks
@product_router.get('/api/tasks')
def hosted_tasks(request:Request):return {'tasks':_list('task',{'project_id':_pid(request)})}
@product_router.post('/api/tasks')
async def hosted_task_create(request:Request):return _create('task',{'project_id':_pid(request)},await _body(request))
@product_router.patch('/api/tasks/{tid}')
async def hosted_task_update(tid:str,request:Request):return _update('task',{'project_id':_pid(request),'task_id':tid},await _body(request))
@product_router.delete('/api/tasks/{tid}')
def hosted_task_delete(tid:str,request:Request):return _delete('task',{'project_id':_pid(request),'task_id':tid})
@product_router.get('/api/tasks/{tid}/milestones')
def hosted_milestones(tid:str,request:Request):return {'milestones':_list('task_milestone',{'project_id':_pid(request),'task_id':tid})}
@product_router.post('/api/tasks/{tid}/milestones')
async def hosted_milestone_create(tid:str,request:Request):return _create('task_milestone',{'project_id':_pid(request),'task_id':tid},await _body(request))

# Canvases
@product_router.post('/api/canvases')
async def hosted_canvas_create(request:Request):return _create('canvas',{'project_id':_pid(request)},await _body(request))
@product_router.get('/api/canvases/{cid}')
def hosted_canvas_get(cid:str,request:Request):
    pid=_pid(request);return {'canvas':_get('canvas',{'project_id':pid,'canvas_id':cid}),'nodes':_list('canvas_node',{'project_id':pid,'canvas_id':cid}),'edges':_list('canvas_edge',{'project_id':pid,'canvas_id':cid})}
@product_router.patch('/api/canvases/{cid}')
async def hosted_canvas_update(cid:str,request:Request):return _update('canvas',{'project_id':_pid(request),'canvas_id':cid},await _body(request))
@product_router.delete('/api/canvases/{cid}')
def hosted_canvas_delete(cid:str,request:Request):return _delete('canvas',{'project_id':_pid(request),'canvas_id':cid})
@product_router.post('/api/canvases/{cid}/nodes')
async def hosted_canvas_node(cid:str,request:Request):return _create('canvas_node',{'project_id':_pid(request),'canvas_id':cid},await _body(request))
@product_router.post('/api/canvases/{cid}/edges')
async def hosted_canvas_edge(cid:str,request:Request):return _create('canvas_edge',{'project_id':_pid(request),'canvas_id':cid},await _body(request))

# Story maps
@product_router.get('/api/maps')
def hosted_maps(request:Request):return {'maps':_list('story_map',{'project_id':_pid(request)})}
@product_router.post('/api/maps')
async def hosted_map_create(request:Request):return _create('story_map',{'project_id':_pid(request)},await _body(request))
@product_router.get('/api/maps/{mid}')
def hosted_map_get(mid:str,request:Request):
    pid=_pid(request);lines=_list('map_line',{'project_id':pid,'map_id':mid});return {'map':_get('story_map',{'project_id':pid,'map_id':mid}),'lines':lines,'junctions':_list('map_junction',{'project_id':pid,'map_id':mid})}
@product_router.patch('/api/maps/{mid}')
async def hosted_map_update(mid:str,request:Request):return _update('story_map',{'project_id':_pid(request),'map_id':mid},await _body(request))
@product_router.delete('/api/maps/{mid}')
def hosted_map_delete(mid:str,request:Request):return _delete('story_map',{'project_id':_pid(request),'map_id':mid})
@product_router.post('/api/maps/{mid}/lines')
async def hosted_map_line(mid:str,request:Request):return _create('map_line',{'project_id':_pid(request),'map_id':mid},await _body(request))
@product_router.post('/api/map-lines/{lid}/items')
async def hosted_map_item(lid:str,request:Request):
    body=await _body(request);mid=str(body.get('map_id') or '');return _create('map_item',{'project_id':_pid(request),'map_id':mid,'line_id':lid},body)
@product_router.post('/api/maps/{mid}/junctions')
async def hosted_map_junction(mid:str,request:Request):return _create('map_junction',{'project_id':_pid(request),'map_id':mid},await _body(request))

# Reviews / questionnaires
@product_router.get('/api/reviews')
def hosted_reviews(request:Request):return {'reviews':_list('review',{'project_id':_pid(request)})}
@product_router.post('/api/reviews')
async def hosted_review_create(request:Request):return _create('review',{'project_id':_pid(request)},await _body(request))
@product_router.get('/api/reviews/{rid}')
def hosted_review_get(rid:str,request:Request):
    pid=_pid(request);return {'review':_get('review',{'project_id':pid,'review_id':rid}),'participants':_list('review_participant',{'project_id':pid,'review_id':rid})}
@product_router.patch('/api/reviews/{rid}')
async def hosted_review_update(rid:str,request:Request):return _update('review',{'project_id':_pid(request),'review_id':rid},await _body(request))
@product_router.delete('/api/reviews/{rid}')
def hosted_review_delete(rid:str,request:Request):return _delete('review',{'project_id':_pid(request),'review_id':rid})
@product_router.post('/api/reviews/{rid}/participants')
async def hosted_review_participant(rid:str,request:Request):return _create('review_participant',{'project_id':_pid(request),'review_id':rid},await _body(request))
@product_router.get('/api/questionnaires')
def hosted_questionnaires(request:Request):return {'questionnaires':_list('questionnaire',{'project_id':_pid(request)})}
@product_router.post('/api/questionnaires')
async def hosted_questionnaire_create(request:Request):return _create('questionnaire',{'project_id':_pid(request)},await _body(request))

# Checkpoints / revisions
@product_router.get('/api/checkpoints')
def hosted_checkpoints(request:Request):return {'checkpoints':_list('checkpoint',{'project_id':_pid(request)})}
@product_router.post('/api/checkpoints')
async def hosted_checkpoint_create(request:Request):
    body=await _body(request)
    if 'snapshot' not in body:
        try:body['snapshot']=_project_snapshot(_pid(request))
        except Exception as exc:raise _error(exc)
    return _create('checkpoint',{'project_id':_pid(request)},body)
@product_router.get('/api/checkpoints/{cid}')
def hosted_checkpoint_get(cid:str,request:Request):return _get('checkpoint',{'project_id':_pid(request),'checkpoint_id':cid})
@product_router.get('/api/history/v2')
def hosted_history_list():return {'revisions':runtime.history.list_revisions()}
@product_router.post('/api/autocommit')
async def hosted_autocommit(request:Request):
    body=await _body(request)
    try:snapshot=_project_snapshot(_pid(request))
    except Exception as exc:raise _error(exc)
    envelope=json.dumps({'snapshot':snapshot},sort_keys=True,separators=(',',':')).encode('utf-8')
    import gzip
    if not hasattr(runtime.history,'create_revision'):raise HTTPException(501,detail='HOSTED_HISTORY_WRITE_UNAVAILABLE')
    return runtime.history.create_revision(gzip.compress(envelope,mtime=0),{'reason':str(body.get('reason') or 'autocommit')})

# Local-destination backup surfaces are intentionally capability-disabled in M5.
@product_router.api_route('/api/backup-destinations{rest:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
@product_router.api_route('/api/backups{rest:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
def hosted_local_backup_unavailable(rest:str=''):
    raise HTTPException(409,detail='HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9')



# Unported multi-record editor features must never fall back to a best-effort
# sequence of CRUD calls. They are explicitly unavailable in hosted mode until
# a single-commit operation contract is implemented (M8B audit boundary).
@product_router.put('/api/scenes/{scene_id}/blocks')
@product_router.post('/api/scenes/{scene_id}/remove-blocks')
@product_router.post('/api/canvases/{canvas_id}/populate')
@product_router.post('/api/maps/{map_id}/populate')
@product_router.post('/api/map-lines/{line_id}/populate')
def hosted_unported_bulk_mutation():
    raise HTTPException(409,detail='HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE')
