from __future__ import annotations

"""Request binding, hosted guards, browser-edit coordination, and startup project boot."""

import ipaddress
import os
import re
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from auth import identity as identity_service
from auth.sessions import AuthenticationError
from backend import client_leases, diagnostics, portable_review_runtime, recovery_runtime
from backend.environment import runtime, _HOSTED_LIMITS
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _enforce_hosted_project_storage
from backend.dependencies import feature_call
from security import hosted_guardrails
from security.http_request import canonical_request_path
from storage import context as storage_context
from storage import database as storage_database
from workspace.models import WorkspaceError

_HOSTED_RATE_LIMITER=hosted_guardrails.HostedRateLimiter()
_AUTH_STARTS_PER_MINUTE=60
_GCP_GFE_PROXY_NETWORKS=(
    ipaddress.ip_network('35.191.0.0/16'),
    ipaddress.ip_network('130.211.0.0/22'),
)

def _rate_limit_client_identity(request: Request) -> str:
    """Return a stable network identity without trusting attacker-supplied XFF.

    The staging VM is reachable only from Google's documented GFE proxy ranges. A
    global external Application Load Balancer appends ``client-ip, forwarding-rule-ip``
    to any pre-existing X-Forwarded-For value. Therefore the second-to-last address is
    authoritative *only* when the direct TCP peer is a GFE. Development/local traffic
    never consumes XFF here.
    """
    peer=str(getattr(request.client,'host','') or '').strip()
    try:
        peer_ip=ipaddress.ip_address(peer)
    except ValueError:
        return 'peer:unknown'
    if runtime.mode!='hosted' or not any(peer_ip in network for network in _GCP_GFE_PROXY_NETWORKS):
        return f'peer:{peer_ip.compressed}'
    forwarded=[]
    for raw_name,raw_value in request.scope.get('headers') or []:
        if bytes(raw_name).lower()!=b'x-forwarded-for':continue
        try:forwarded.extend(part.strip() for part in bytes(raw_value).decode('ascii').split(','))
        except UnicodeDecodeError:return f'gfe:{peer_ip.compressed}'
    forwarded=[part for part in forwarded if part]
    if len(forwarded)<2:
        return f'gfe:{peer_ip.compressed}'
    try:
        # GFE appends these two values after all client-supplied XFF content.
        client_ip=ipaddress.ip_address(forwarded[-2])
        ipaddress.ip_address(forwarded[-1])
    except ValueError:
        return f'gfe:{peer_ip.compressed}'
    return f'client:{client_ip.compressed}'
_prune_expired_temporary_portable_reviews = feature_call('reviews.portable','_prune_expired_temporary_portable_reviews')

_PROJECT_CONTEXT_FREE_EXACT={
    '/api/runtime','/api/health','/api/workspace','/api/recovery/status','/api/data/export',
    '/api/features','/api/metro-view-config','/api/locales','/api/auth/session','/api/auth/logout',
}
_PROJECT_CONTEXT_FREE_PREFIXES=(
    '/api/profile','/api/profiles','/api/danger/','/api/trash/','/api/locales','/api/portable-review','/api/auth/security',
)

def _request_project_identity(request:Request) -> str:
    """Resolve one request's project without treating process-global Current as authority.

    Project-resource routes bind to the resource project itself. Browser APIs that do
    not encode the project in their path carry X-Breadmaker-Project (or project_id for
    raw image/download requests). Global/recovery/account endpoints intentionally do
    not require a readable project so Project Settings and Delete All remain reachable.
    """
    path=canonical_request_path(request)
    # Workspace discovery is normally project-free so recovery and zero-project
    # account shells stay reachable. A routed browser tab may nevertheless bind its
    # explicit project here; that makes the tab's selection independent from the
    # installation-wide default without turning an omitted header into authority.
    if path=='/api/workspace':
        explicit=(request.headers.get('x-breadmaker-project') or request.query_params.get('project_id') or '').strip()
        return explicit[:128] if explicit else ''
    if path in _PROJECT_CONTEXT_FREE_EXACT or any(path.startswith(prefix) for prefix in _PROJECT_CONTEXT_FREE_PREFIXES):
        return ''
    match=re.match(r'^/api/projects/([0-9a-f]{32})(?:/|$)',path)
    if match:return unquote(match.group(1))[:128]
    explicit=(request.headers.get('x-breadmaker-project') or request.query_params.get('project_id') or '').strip()
    if explicit:return explicit[:128]
    if portable_review_runtime.current_portable_review_path() is not None:return ''
    if runtime.sessions.requires_authentication:
        principal=runtime.sessions.current()
        if principal is None:return ''
        # Account sessions never inherit the process-global shell project. Missing
        # project context resolves only inside the authenticated account's memberships.
        try:return str(runtime.projects.profile_project_target(principal.user_id) or '')
        except Exception:return ''
    return str(runtime.projects.active_id() or '')


def _request_user_identity(request:Request) -> str:
    explicit=(request.headers.get('x-breadmaker-user') or request.query_params.get('user_id') or '').strip()[:128] or None
    try:return str(runtime.identity.request_user_id(explicit) or '')
    except AuthenticationError:raise
    except identity_service.AuthorizationError:return ''
    except Exception:return ''


_AUTH_PUBLIC_EXACT={
    '/api/runtime','/api/health','/api/features','/api/metro-view-config','/api/locales',
    '/api/auth/session','/api/auth/logout','/api/auth/csrf','/api/auth/client-config','/api/auth/session/exchange',
}

@asynccontextmanager
async def _request_binding(provider,*args,**kwargs):
    # Local storage remains on its existing binding path. Hosted providers
    # resolve blocking authority in bounded workers, then bind task-local state.
    asynchronous=getattr(provider,'bind_async',None) if runtime.mode in {'hosted','hosted_dev'} else None
    if asynchronous is not None:
        async with asynchronous(*args,**kwargs) as value:yield value
    else:
        with provider.bind(*args,**kwargs) as value:yield value


def _runtime_bootstrap_payload() -> dict:
    """Return project-free runtime metadata used before any project/profile binding.

    This payload is intentionally safe to compute when Current is missing, corrupt, or
    unsupported. Startup must be able to learn runtime/auth capabilities before touching
    installation-local identity or project state.
    """
    return {
        "mode":runtime.mode,
        "capabilities":runtime.capabilities.public(),
        "metadata":runtime.capabilities.metadata(),
        "auth":runtime.sessions.metadata(),
        "project_header":"X-Breadmaker-Project",
        "actor_header":None if runtime.sessions.requires_authentication else "X-Breadmaker-User",
    }

async def local_origin_guard(request: Request, call_next):
    path=canonical_request_path(request)
    # Runtime discovery is the first browser bootstrap request and is deliberately
    # independent of Current, local profiles, membership sidecars, Recovery, and leases.
    # Return it before request identity/project binding so damaged or stale installation
    # state can never turn this global capability endpoint into a project-context 404.
    if request.method.upper() in {"GET","HEAD"} and path=='/api/runtime':
        return JSONResponse(_runtime_bootstrap_payload(),headers={'Cache-Control':'no-store, max-age=0'})

    # Breadmaker is intentionally localhost-only. A hostile webpage can still submit
    # a plain HTML form to localhost, so protect mutating API routes from CSRF.
    is_api=path.startswith('/api/')
    mutating=is_api and request.method.upper() not in {"GET","HEAD","OPTIONS"}
    real_auth = runtime.sessions.requires_authentication and runtime.sessions.metadata().get('auth_mode')=='identity_platform'
    if real_auth and request.method.upper()=='GET' and path in {'/auth/login','/auth/signup','/auth/language'}:
        client_host=_rate_limit_client_identity(request)
        allowed,retry=_HOSTED_RATE_LIMITER.allow(client_host,'auth-start',limit=_AUTH_STARTS_PER_MINUTE)
        if not allowed:
            return JSONResponse({'detail':'AUTH_START_RATE_LIMITED'},status_code=429,headers={'Retry-After':str(retry),'Cache-Control':'no-store'})
    if _HOSTED_LIMITS is not None and is_api and request.method.upper() not in {"GET","HEAD","OPTIONS"}:
        limit=hosted_guardrails.body_limit_for_path(path,_HOSTED_LIMITS)
        raw_length=str(request.headers.get('content-length') or '').strip()
        if raw_length:
            try:declared=int(raw_length)
            except ValueError:return JSONResponse({'detail':'REQUEST_CONTENT_LENGTH_INVALID'},status_code=400)
            if declared<0 or declared>limit:
                return JSONResponse({'detail':{'code':'HOSTED_REQUEST_TOO_LARGE','message':'This request exceeds the hosted resource limit.','params':{'max_bytes':limit}}},status_code=413)
    if mutating:
        origin=request.headers.get('origin')
        if runtime.mode=='local':
            if origin:
                try:host=(urlparse(origin).hostname or '').lower()
                except Exception:host=''
                if host not in {'127.0.0.1','localhost','::1'}:
                    return JSONResponse({'detail':'Breadmaker only accepts changes from its local app.'},status_code=403)
        else:
            if real_auth and not origin:
                return JSONResponse({'detail':'Breadmaker hosted authenticated changes require an Origin header.'},status_code=403)
            # Hosted browser mutations are protected independently from identity.
            # Compare the complete Origin (scheme + authority), using the configured
            # public origin in real hosted auth so reverse-proxy transport is not
            # mistaken for browser authority.
            if origin:
                try:
                    origin_parts=urlparse(origin)
                    configured_origin=str(os.environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip().rstrip('/')
                    if configured_origin:
                        expected_parts=urlparse(configured_origin)
                        expected=(expected_parts.scheme.lower(),expected_parts.netloc.lower())
                    else:
                        expected=(str(request.url.scheme or '').lower(),str(request.headers.get('host') or '').lower())
                    supplied=(origin_parts.scheme.lower(),origin_parts.netloc.lower())
                    origin_shape_valid=(
                        supplied[0] in {'http','https'} and bool(supplied[1])
                        and origin_parts.path in {'','/'} and not origin_parts.params
                        and not origin_parts.query and not origin_parts.fragment
                        and origin_parts.username is None and origin_parts.password is None
                    )
                except Exception:
                    supplied=('', '');expected=('','');origin_shape_valid=False
                # Scheme is part of an origin. Never treat http://app and https://app as
                # equivalent merely because the Host matches; production uses the exact
                # configured public origin so reverse-proxy transport cannot weaken CSRF.
                if not origin_shape_valid or supplied!=expected:
                    return JSONResponse({'detail':'Breadmaker hosted requests must come from the same origin.'},status_code=403)
            fetch_site=str(request.headers.get('sec-fetch-site') or '').lower()
            if fetch_site and fetch_site not in {'same-origin','none'}:
                return JSONResponse({'detail':'Breadmaker hosted requests must come from the same origin.'},status_code=403)

    session_token=request.cookies.get(runtime.sessions.cookie_name)
    try:
        async with _request_binding(runtime.sessions,session_token):
            # Opaque-session resolution prunes expired sessions first. Mirror that
            # lifecycle into detached Portable Review state so expired sessions cannot
            # leave package bytes/passphrases resident until process restart.
            _prune_expired_temporary_portable_reviews()
            protected_api=is_api and path not in _AUTH_PUBLIC_EXACT
            if protected_api and runtime.sessions.requires_authentication and runtime.sessions.current() is None:
                return JSONResponse({'detail':'AUTHENTICATION_REQUIRED'},status_code=401,headers={'Cache-Control':'private, no-store'})
            if _HOSTED_LIMITS is not None and is_api:
                principal=runtime.sessions.current();client_host=_rate_limit_client_identity(request)
                rate_identity=str(principal.user_id if principal is not None else client_host)
                if hosted_guardrails.is_heavy_request(path,request.method):
                    allowed,retry=_HOSTED_RATE_LIMITER.allow(rate_identity,'heavy',limit=_HOSTED_LIMITS.heavy_per_minute)
                    if not allowed:return JSONResponse({'detail':{'code':'HOSTED_RATE_LIMITED','message':'Too many expensive Breadmaker operations. Try again shortly.','params':{}}},status_code=429,headers={'Retry-After':str(retry),'Cache-Control':'private, no-store'})
                if mutating:
                    allowed,retry=_HOSTED_RATE_LIMITER.allow(rate_identity,'write',limit=_HOSTED_LIMITS.writes_per_minute)
                    if not allowed:return JSONResponse({'detail':{'code':'HOSTED_RATE_LIMITED','message':'Too many Breadmaker changes. Try again shortly.','params':{}}},status_code=429,headers={'Retry-After':str(retry),'Cache-Control':'private, no-store'})
            project_id=(await run_in_threadpool(_request_project_identity,request) if runtime.mode in {'hosted','hosted_dev'} else _request_project_identity(request)) if is_api else ''
            user_id=_request_user_identity(request) if is_api and (not runtime.sessions.requires_authentication or runtime.sessions.current() is not None) else ''
            request.state.breadmaker_project_id=project_id or None
            request.state.breadmaker_user_id=user_id or None
            request.state.breadmaker_session=runtime.sessions.current()
            context_manager=_request_binding(runtime.context,project_id,user_id=user_id or None) if project_id else nullcontext(None)
            async with context_manager:
                if _HOSTED_LIMITS is not None and mutating and project_id and path not in {'/api/projects','/api/projects/import','/api/projects/import-json','/api/import/new-project'}:
                    raw_length=str(request.headers.get('content-length') or '').strip();declared=0
                    if raw_length:
                        try:declared=max(0,int(raw_length))
                        except ValueError:declared=0
                    if runtime.mode in {'hosted','hosted_dev'}:
                        await run_in_threadpool(_enforce_hosted_project_storage,project_id=project_id,additional_bytes=declared)
                    else:_enforce_hosted_project_storage(project_id=project_id,additional_bytes=declared)
                if mutating:
                    client=(request.headers.get("x-breadmaker-client") or '').strip()[:120] or None
                    if client:
                        actor=runtime.context.current().actor if runtime.context.current() is not None else None
                        if not client_leases._claim_client_lease(project_id,client,actor_id=str(getattr(actor,'user_id','') or ''),actor_role=str(getattr(actor,'role','') or '')):
                            return JSONResponse({"detail":"Another Breadmaker browser tab is already editing this project. Close that tab or wait briefly, then reload."},status_code=409)
                    elif client_leases._active_foreign_client(project_id,None):
                        return JSONResponse({"detail":"Breadmaker is currently controlled by an active editor tab."},status_code=409)
                response=await call_next(request)
                if is_api and runtime.sessions.requires_authentication and runtime.sessions.current() is not None:
                    # Hosted account APIs carry manuscript/project data behind an opaque
                    # cookie. Never let a browser, reverse proxy, or future CDN treat an
                    # authenticated response as a shared cache object.
                    response.headers['Cache-Control']='private, no-store'
                return response
    except AuthenticationError:
        return JSONResponse({'detail':'AUTHENTICATION_REQUIRED'},status_code=401,headers={'Cache-Control':'private, no-store'})
    except identity_service.AuthorizationError:
        # In account-hosted mode project membership is also an isolation boundary. Do
        # not reveal whether an arbitrary project id exists to a non-member. Permission
        # denials raised *inside* an authorized project remain normal HTTP 403 responses.
        if runtime.sessions.requires_authentication:
            return JSONResponse({"detail":"PROJECT_NOT_FOUND"},status_code=404,headers={'Cache-Control':'private, no-store'})
        return JSONResponse({"detail":"PROJECT_MEMBERSHIP_REQUIRED"},status_code=403)
    except WorkspaceError as exc:
        headers={'Cache-Control':'private, no-store'} if runtime.sessions.requires_authentication else None
        return JSONResponse({"detail":str(exc)},status_code=404,headers=headers)
    except RuntimeError as exc:
        detail=str(exc)
        headers={'Cache-Control':'private, no-store'} if runtime.sessions.requires_authentication else None
        if detail in {'PROJECT_CONTEXT_REQUIRED','PROJECT_CONTEXT_NOT_FOUND'}:
            return JSONResponse({"detail":detail},status_code=404,headers=headers)
        if detail=='Another Breadmaker Editor process is already using this project folder.':
            return JSONResponse({"detail":detail},status_code=409,headers=headers)
        raise


def startup():
    runtime.projects.init()
    # Session-authenticated hosted runtimes have no process-global user at startup.
    # Projects are opened lazily under the authenticated request actor; request context
    # acquires the project lock and Recovery scheduler when that project is actually used.
    if runtime.capabilities.supports('account_sessions'):
        recovery_runtime.set_recovery_mode(None)
        return
    if not runtime.projects.list():
        candidates=runtime.projects.recovery_candidates()
        if candidates:
            candidate=candidates[0]; storage_context.configure_project(Path(candidate['path']))
            occurrence=diagnostics.log_event("RECOVERY_BOOT_002","Breadmaker found project data that could not be opened safely and did not replace it with a blank project.",level="ERROR",context={"path":candidate['path']},data_safe=False)
            recovery_runtime.set_recovery_mode({"active_project_id":candidate.get('id'),"active_project_name":None,"diagnostic":occurrence,"message":"Breadmaker found project data that needs recovery. No replacement project was created."}); return
        created=runtime.projects.create("Untitled Book",activate=False)
        startup_project_id=created['id']
    else:
        startup_project_id=runtime.projects.active_id()
    try:
        _activate_project(startup_project_id); recovery_runtime.set_recovery_mode(None)
    except Exception as exc:
        storage_database.release_instance_lock()
        # Lock contention means another healthy Breadmaker server owns this local
        # workspace. It is not project damage and must not expose a misleading
        # Recovery screen from a second, unusable process.
        if isinstance(exc,RuntimeError) and str(exc)=='Another Breadmaker Editor process is already using this project folder.':
            diagnostics.log_event("LOCAL_PROCESS_LOCK_001","Breadmaker did not start because another Editor process owns the active project.",level="ERROR",error=exc,data_safe=True)
            raise
        occurrence=diagnostics.log_event("RECOVERY_BOOT_001","Breadmaker entered recovery mode because the active project could not be opened safely.",level="ERROR",error=exc,data_safe=True)
        active=runtime.projects.active()
        recovery_runtime.set_recovery_mode({"active_project_id":runtime.projects.active_id(),"active_project_name":active.get('name') if active else None,"diagnostic":occurrence,"message":"Breadmaker could not safely open the active project. No project data was changed."})

def install(app: FastAPI) -> None:
    app.middleware("http")(local_origin_guard)
