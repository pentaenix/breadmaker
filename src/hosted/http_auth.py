from __future__ import annotations

"""Hosted account-authentication routes.

Identity Platform is the sole hosted authentication authority. Browser provider SDKs
are transient proof mechanisms only; Breadmaker authority remains the verified server cookie.
"""

import os
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.environment import runtime
from backend.runtime_contracts import SecuritySessionIn

router=APIRouter()


_IDENTITY_PLATFORM = runtime.mode in {'hosted_dev','hosted'} and runtime.sessions.metadata().get('auth_mode')=='identity_platform'
if _IDENTITY_PLATFORM:
    from auth.identity_platform import IdentityPlatformError, csrf_matches, new_csrf_token
    from auth.identity_platform_security import IdentityPlatformSecurityService

    _IP_CSRF_COOKIE='__Host-breadmaker_csrf' if runtime.sessions.cookie_secure else 'breadmaker_csrf'
    _IP_SECURITY=IdentityPlatformSecurityService(runtime.sessions,runtime.accounts)

    class IdentityPlatformExchangeIn(BaseModel):
        id_token: str = Field(alias='idToken')
        invitation_token: str | None = Field(default=None,alias='invitationToken')

    class IdentityPlatformProofIn(BaseModel):
        id_token: str = Field(alias='idToken')
        purpose: str

    def _ip_no_store_headers():
        return {'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'no-referrer'}

    def _ip_bool(name:str,default:bool=False)->bool:
        raw=str(os.environ.get(name) or '').strip().lower()
        if not raw:return default
        return raw in {'1','true','yes','on'}

    def _ip_client_config()->dict:
        project=str(os.environ.get('BREADMAKER_GCP_PROJECT_ID') or os.environ.get('BREADMAKER_GCP_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT') or '').strip()
        api_key=str(os.environ.get('BREADMAKER_FIREBASE_WEB_API_KEY') or '').strip()
        auth_domain=str(os.environ.get('BREADMAKER_FIREBASE_AUTH_DOMAIN') or '').strip()
        if not auth_domain:
            origin=str(os.environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip()
            auth_domain=str(urlparse(origin).hostname or '')
        if not project or not api_key or not auth_domain:
            raise HTTPException(503,detail='IDENTITY_PLATFORM_CLIENT_CONFIG_INCOMPLETE')
        return {
            'firebase':{'apiKey':api_key,'authDomain':auth_domain,'projectId':project},
            'sdk_version':'13.0.0',
            'providers':{
                'google':_ip_bool('BREADMAKER_IDENTITY_GOOGLE_ENABLED',False),
                'apple':_ip_bool('BREADMAKER_IDENTITY_APPLE_ENABLED',False),
                'email_password':True,
            },
            'policy':{
                'client_persistence':'in_memory',
                'email_password_totp_required':True,
                'social_totp_optional_until_enrolled':True,
                'explicit_linking_only':True,
                'fresh_reauthentication_seconds':300,
                'email_is_authority':False,
            },
        }

    @router.get('/auth/login',response_class=HTMLResponse)
    def identity_platform_login_shell():
        # The page intentionally contains no provider credentials/tokens and no inline
        # script.  The Web SDK exists only long enough to prove identity and exchange a
        # fresh ID token for Breadmaker's HttpOnly server cookie.
        return HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Sign in · Breadmaker</title>'
            '<link rel="stylesheet" href="/static/css/account/identity-platform.css?v=10.5.47">'
            '</head><body><main id="identityLogin" class="identity-login" aria-live="polite">'
            '<div class="identity-card"><div class="identity-brand"><strong>Breadmaker</strong></div>'
            '<h1>Sign in</h1>'
            '<div id="identityMessage" class="identity-message" hidden></div>'
            '<div id="identitySocial" class="identity-social"></div>'
            '<form id="identityEmailForm" autocomplete="on">'
            '<label>Email<input id="identityEmail" type="email" autocomplete="email" required></label>'
            '<label>Password<input id="identityPassword" type="password" autocomplete="current-password" minlength="8" required></label>'
            '<button id="identityEmailSignIn" type="submit">Sign in with email</button>'
            '<button id="identityEmailSignUp" type="button" class="quiet">Create account</button>'
            '<button id="identityForgotPassword" type="button" class="linkish">Forgot password?</button>'
            '</form>'
            '<div id="identityTotp" class="identity-totp" hidden></div>'
            '</div></main>'
            '<script type="module" src="/static/js/features/account/identity-platform.js?v=10.5.47"></script>'
            '</body></html>',
            headers=_ip_no_store_headers(),
        )

    @router.get('/api/auth/client-config')
    def identity_platform_client_config():
        return JSONResponse(_ip_client_config(),headers=_ip_no_store_headers())

    @router.get('/api/auth/csrf')
    def identity_platform_csrf():
        token=new_csrf_token()
        response=JSONResponse({'csrf_token':token},headers=_ip_no_store_headers())
        response.set_cookie(
            _IP_CSRF_COOKIE,token,max_age=600,path='/',secure=runtime.sessions.cookie_secure,
            httponly=True,samesite='strict',
        )
        return response

    @router.post('/api/auth/session/exchange')
    def identity_platform_exchange(payload:IdentityPlatformExchangeIn,request:Request):
        if not csrf_matches(request.cookies.get(_IP_CSRF_COOKIE),request.headers.get('x-csrf-token')):
            raise HTTPException(403,detail='CSRF_REJECTED')
        exchange=getattr(runtime.sessions,'exchange_id_token',None)
        if not callable(exchange):
            raise HTTPException(503,detail='IDENTITY_PLATFORM_SESSION_EXCHANGE_UNAVAILABLE')
        try:
            result=exchange(payload.id_token,invitation_token=payload.invitation_token)
        except IdentityPlatformError as exc:
            code=str(exc) or 'IDENTITY_PLATFORM_EXCHANGE_REJECTED'
            status=403 if code.startswith('PRIVATE_ALPHA_') or code in {'VERIFIED_EMAIL_REQUIRED','RECENT_AUTH_REQUIRED','TOTP_ENROLLMENT_REQUIRED'} else 401
            raise HTTPException(status,detail=code) from exc
        response=JSONResponse({
            'authenticated':True,
            'auth_method':result.principal.auth_method,
            'authenticated_at':result.principal.authenticated_at,
        },headers=_ip_no_store_headers())
        response.set_cookie(
            runtime.sessions.cookie_name,result.cookie,max_age=runtime.sessions.max_age_seconds,path='/',
            secure=runtime.sessions.cookie_secure,httponly=True,samesite='lax',
        )
        response.delete_cookie(_IP_CSRF_COOKIE,path='/',secure=runtime.sessions.cookie_secure,samesite='strict')
        return response

    @router.get('/api/auth/security')
    def identity_platform_security_snapshot():
        principal=runtime.sessions.current()
        if principal is None:raise HTTPException(401,detail='AUTHENTICATION_REQUIRED')
        try:
            payload=_IP_SECURITY.snapshot(principal)
            payload['provider_availability']=dict(_ip_client_config().get('providers') or {})
            return JSONResponse(payload,headers=_ip_no_store_headers())
        except IdentityPlatformError as exc:raise HTTPException(409,detail=str(exc)) from exc

    @router.post('/api/auth/security/proof')
    def identity_platform_security_proof(payload:IdentityPlatformProofIn,request:Request):
        if not csrf_matches(request.cookies.get(_IP_CSRF_COOKIE),request.headers.get('x-csrf-token')):raise HTTPException(403,detail='CSRF_REJECTED')
        principal=runtime.sessions.current()
        if principal is None:raise HTTPException(401,detail='AUTHENTICATION_REQUIRED')
        try:proof=_IP_SECURITY.verify_fresh_proof(principal,payload.id_token,purpose=payload.purpose)
        except IdentityPlatformError as exc:raise HTTPException(403,detail=str(exc)) from exc
        return {'ok':True,'provider':proof.provider,'authenticated_at':proof.auth_time}

    @router.post('/api/auth/security/sync')
    def identity_platform_security_sync(payload:IdentityPlatformProofIn,request:Request):
        if not csrf_matches(request.cookies.get(_IP_CSRF_COOKIE),request.headers.get('x-csrf-token')):raise HTTPException(403,detail='CSRF_REJECTED')
        principal=runtime.sessions.current()
        if principal is None:raise HTTPException(401,detail='AUTHENTICATION_REQUIRED')
        try:
            snapshot=_IP_SECURITY.sync_after_provider_mutation(principal,payload.id_token,purpose=payload.purpose)
            refreshed=runtime.sessions.exchange_id_token(payload.id_token)
        except IdentityPlatformError as exc:raise HTTPException(409,detail=str(exc)) from exc
        response=JSONResponse({'ok':True,'security':snapshot},headers=_ip_no_store_headers())
        response.set_cookie(
            runtime.sessions.cookie_name,refreshed.cookie,max_age=runtime.sessions.max_age_seconds,path='/',
            secure=runtime.sessions.cookie_secure,httponly=True,samesite='lax',
        )
        return response

    @router.post('/api/auth/logout-everywhere')
    @router.post('/api/auth/security/sign-out-everywhere')
    def identity_platform_logout_everywhere(payload:IdentityPlatformProofIn,request:Request):
        if not csrf_matches(request.cookies.get(_IP_CSRF_COOKIE),request.headers.get('x-csrf-token')):raise HTTPException(403,detail='CSRF_REJECTED')
        principal=runtime.sessions.current()
        if principal is None:raise HTTPException(401,detail='AUTHENTICATION_REQUIRED')
        try:
            _IP_SECURITY.verify_fresh_proof(principal,payload.id_token,purpose='sign_out_all_sessions')
            runtime.sessions.revoke_user(principal.user_id)
        except IdentityPlatformError as exc:raise HTTPException(403,detail=str(exc)) from exc
        response=JSONResponse({'ok':True,'revoked_everywhere':True},headers=_ip_no_store_headers())
        response.delete_cookie(runtime.sessions.cookie_name,path='/',secure=runtime.sessions.cookie_secure,samesite='lax')
        return response

    @router.post('/api/auth/security/session/revoke')
    def identity_platform_selective_session_revoke(_payload:SecuritySessionIn):
        # Firebase session cookies are provider-signed and are not individually
        # enumerable/revocable.  Breadmaker offers current logout and global provider
        # revocation rather than pretending a selective device kill exists.
        raise HTTPException(409,detail='IDENTITY_PLATFORM_SELECTIVE_REVOCATION_UNSUPPORTED')
