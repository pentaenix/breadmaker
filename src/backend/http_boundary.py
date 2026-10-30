from __future__ import annotations

"""Browser shell cache/security headers, platform config routes, and HTTP error translation."""

import base64
import hashlib
import math
import os
import re

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import diagnostics
from security.http_request import canonical_request_path
from backend.configuration import _feature_flags_raw, _metro_view_config_raw
from backend.environment import APP_VERSION, STATIC, runtime

router=APIRouter()

APP_SHELL_PATHS={"/","/canvas","/maps","/tasks","/reviews"}
_PROJECT_SHELL_RE=re.compile(r'^/p/[0-9a-f]{32}(?:/(?:canvas|maps|tasks|reviews))?/?$')
def _index_inline_script_csp_hash() -> str:
    try:
        source=(STATIC/'index.html').read_text(encoding='utf-8')
        match=re.search(r'<script>(.*?)</script>',source,re.S)
        if not match:return ''
        digest=hashlib.sha256(match.group(1).encode('utf-8')).digest()
        return 'sha256-'+base64.b64encode(digest).decode('ascii')
    except Exception:return ''

_INDEX_INLINE_SCRIPT_CSP_HASH=_index_inline_script_csp_hash()

def _security_headers(request:Request) -> dict[str,str]:
    identity_web=runtime.mode in {'hosted','hosted_dev'} and runtime.sessions.metadata().get('auth_mode')=='identity_platform'
    # Firebase popup auth loads gapi.iframes through Google's loader. Allow only
    # its SDK paths, not arbitrary Google hosts or unsafe-inline/unsafe-eval.
    script_policy="'self'"+(f" '{_INDEX_INLINE_SCRIPT_CSP_HASH}'" if _INDEX_INLINE_SCRIPT_CSP_HASH else '')+(" https://www.gstatic.com https://apis.google.com/js/ https://apis.google.com/_/scs/" if identity_web else '')
    if runtime.mode=='local':script_policy+=" 'wasm-unsafe-eval'"
    connect_policy="'self'"+(" https://identitytoolkit.googleapis.com https://securetoken.googleapis.com" if identity_web else '')
    frame_policy="'self'"+(" https://accounts.google.com https://appleid.apple.com https://*.firebaseapp.com https://auth.breadmaker.studio" if identity_web else '')
    headers={
        'Content-Security-Policy':f"default-src 'self'; script-src {script_policy}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src {connect_policy}; frame-src {frame_policy}; media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        'X-Frame-Options':'DENY',
        'X-Content-Type-Options':'nosniff',
        # Firebase's browser-restricted API key needs the HTTPS origin. Never
        # disclose manuscript paths, return_to queries or invitation fragments.
        'Referrer-Policy':'strict-origin' if identity_web else 'no-referrer',
        'Permissions-Policy':'camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()',
    }
    if runtime.mode=='local':
        # Local WASM inference may use shared-memory CPU workers. Hosted identity
        # and cross-origin integrations keep their existing isolation policy.
        headers['Cross-Origin-Opener-Policy']='same-origin'
        headers['Cross-Origin-Embedder-Policy']='require-corp'
    proto=str(request.headers.get('x-forwarded-proto') or request.url.scheme or '').split(',')[0].strip().lower()
    configured_origin=str(os.environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip().lower()
    configured_hosted_https=runtime.mode=='hosted' and configured_origin.startswith('https://')
    if runtime.mode!='local' and (proto=='https' or configured_hosted_https):
        headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    return headers
async def prevent_stale_local_ui_assets(request: Request, call_next):
    """Breadmaker is a local application, not a CDN-backed website.

    The HTML shell and JavaScript must always come from the same installation. Browser
    heuristic caching can otherwise pair a newly installed index.html with an older
    app.js, causing startup-time null DOM bindings and preventing manuscript boot.
    """
    response = await call_next(request)
    path=canonical_request_path(request)
    if path in APP_SHELL_PATHS or _PROJECT_SHELL_RE.fullmatch(path) or path.startswith("/static/") or path.startswith("/locales/"):
        # Only public, release-stamped code/styles may persist in the hosted
        # browser cache. Never cache shells, account data, locales or manuscripts.
        stamped=(runtime.mode in {'hosted','hosted_dev'}
            and request.method.upper() in {'GET','HEAD'} and response.status_code==200
            and path.startswith('/static/') and path.endswith(('.css','.js'))
            and list(request.query_params.multi_items())==[('v',APP_VERSION)])
        if stamped:
            response.headers['Cache-Control']='public, max-age=31536000, immutable'
            for header in ('Pragma','Expires'):
                if header in response.headers:del response.headers[header]
        else:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
    for name,value in _security_headers(request).items():response.headers[name]=value
    return response




@router.get("/api/features")
def feature_flags():
    return _feature_flags_raw()



@router.get("/api/metro-view-config")
def metro_view_config():
    return _metro_view_config_raw()

def _validation_json_safe(value):
    if value is None or isinstance(value,(str,int,bool)): return value
    if isinstance(value,float): return value if math.isfinite(value) else str(value)
    if isinstance(value,dict): return {str(k):_validation_json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)): return [_validation_json_safe(v) for v in value]
    return str(value)

async def request_validation_exception_handler(_request: Request, exc: RequestValidationError):
    # Starlette's JSON response correctly rejects NaN/Infinity, but Python's JSON
    # request parser accepts those extensions. Sanitize validation diagnostics so
    # malformed numeric input receives a normal 422 instead of crashing the handler.
    errors = _validation_json_safe(exc.errors())
    if runtime.mode != 'local':
        # Pydantic error payloads can contain `input` (including token fields,
        # passwords or manuscript text) and nested `ctx` exception instances.
        # Neither belongs in hosted error responses or proxy captures.
        errors = [
            {"type":str(item.get("type") or "validation_error")[:80],
             "loc":[str(part)[:80] for part in (item.get("loc") or [])][:12]}
            for item in (errors if isinstance(errors,list) else [])
            if isinstance(item,dict)
        ][:32]
    return JSONResponse(status_code=422,content={"detail":{
        "code":"VALIDATION_001",
        "message":"The request could not be validated.",
        "params":{"errors":errors},
    }})

async def http_exception_localization_bridge(_request: Request, exc: HTTPException):
    """Preserve existing HTTP errors while exposing stable Breadmaker error codes structurally.

    Existing endpoints historically encoded codes as ``CODE: English fallback`` strings.
    7.0.5 keeps the fallback for diagnostics/older clients but lets the browser localize
    coded errors without teaching the Python backend about the active UI language.
    """
    detail=exc.detail
    if isinstance(detail,str):
        match=re.match(r"^([A-Z][A-Z0-9_]*_[0-9]+):\s*(.*)$",detail,re.S)
        if match:
            detail={"code":match.group(1),"message":match.group(2),"params":{}}
    return JSONResponse(status_code=exc.status_code,content={"detail":_validation_json_safe(detail)},headers=exc.headers)

async def unhandled_exception_handler(request: Request, exc: Exception):
    occurrence=diagnostics.log_event(
        "UNHANDLED_001", "Breadmaker encountered an unexpected server error; the current transaction was rolled back.",
        level="ERROR", context={"method":request.method,"path":canonical_request_path(request)}, error=exc, data_safe=True)
    return JSONResponse(status_code=500,content={"detail":{
        "code":"UNHANDLED_001",
        "message":"Breadmaker could not complete this action. The active database transaction was rolled back. Diagnostic {diagnostic}.",
        "params":{"diagnostic":occurrence},
    }})

def install(app: FastAPI) -> None:
    # Registration order intentionally matches the historical monolith. The request
    # binding middleware is installed later by request_pipeline.install().
    app.middleware("http")(prevent_stale_local_ui_assets)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(router)
    app.add_exception_handler(RequestValidationError,request_validation_exception_handler)
    app.add_exception_handler(HTTPException,http_exception_localization_bridge)
    app.add_exception_handler(Exception,unhandled_exception_handler)
