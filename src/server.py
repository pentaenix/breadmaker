from __future__ import annotations

import json
import os
import urllib.parse

from . import app as core

from backend import environment, request_pipeline
from security import hosted_guardrails




def _hosted_body_limit(scope) -> int | None:
    limits=environment._HOSTED_LIMITS
    if limits is None or scope.get('type')!='http':return None
    method=str(scope.get('method') or '').upper()
    if method in {'GET','HEAD','OPTIONS'}:return None
    path=str(scope.get('path') or '')
    return int(hosted_guardrails.body_limit_for_path(path,limits))


async def _send_too_large(send,limit:int) -> None:
    payload=json.dumps({'detail':{'code':'HOSTED_REQUEST_TOO_LARGE','message':'This request exceeds the hosted resource limit.','params':{'max_bytes':int(limit)}}},separators=(',',':')).encode('utf-8')
    await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'private, no-store'),(b'x-content-type-options',b'nosniff')]})
    await send({'type':'http.response.body','body':payload})


async def _serve_with_hosted_body_limit(scope,receive,send,limit:int) -> None:
    """Bound streamed/chunked hosted request bodies before framework parsers spool them.

    Content-Length is checked again in the FastAPI middleware, but an attacker is not
    required to send that header. This outer ASGI boundary counts bytes as Uvicorn
    delivers them and turns parser disconnect/error responses into one deterministic
    413. Desktop/local mode never enters this path.
    """
    total=0;exceeded=False;replacement_sent=False;downstream_started=False

    async def bounded_receive():
        nonlocal total,exceeded
        if exceeded:return {'type':'http.disconnect'}
        message=await receive()
        if message.get('type')=='http.request':
            total+=len(message.get('body') or b'')
            if total>limit:
                exceeded=True
                return {'type':'http.disconnect'}
        return message

    async def guarded_send(message):
        nonlocal replacement_sent,downstream_started
        if exceeded:
            if not replacement_sent and not downstream_started:
                replacement_sent=True
                await _send_too_large(send,limit)
            return
        if message.get('type')=='http.response.start':downstream_started=True
        await send(message)

    try:
        await core.app(scope,bounded_receive,guarded_send)
    except BaseException:
        if not exceeded:raise
        if not replacement_sent and not downstream_started:
            await _send_too_large(send,limit);replacement_sent=True
        return
    if exceeded and not replacement_sent and not downstream_started:
        await _send_too_large(send,limit)


async def _send_load_balancer_health(send, *, head: bool = False) -> None:
    """Minimal host-independent health probe for the external load balancer only.

    The real application remains Host-pinned. This outer endpoint intentionally exposes
    no runtime, project, auth, version, or storage metadata, so Google health checkers do
    not need to forge the public browser Host header.
    """
    payload=b'{"ok":true}'
    headers=[
        (b'content-type',b'application/json'),
        (b'cache-control',b'no-store'),
        (b'x-content-type-options',b'nosniff'),
    ]
    if environment.runtime.mode=='hosted':
        headers.append((b'strict-transport-security',b'max-age=31536000; includeSubDomains'))
    await send({'type':'http.response.start','status':200,'headers':headers})
    await send({'type':'http.response.body','body':b'' if head else payload})


def _expected_real_auth_host() -> str:
    """Return the exact configured browser authority when real-auth Host pinning is enabled."""
    if str(os.environ.get('BREADMAKER_ENFORCE_PUBLIC_HOST') or '').strip().lower() not in {'1','true','yes'}:
        return ''
    if not environment.runtime.sessions.requires_authentication or environment.runtime.sessions.metadata().get('auth_mode') != 'identity_platform':
        return ''
    origin = str(os.environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip()
    try:
        parts = urllib.parse.urlsplit(origin)
        if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.path not in {'','/'} or parts.query or parts.fragment:
            return ''
        # Accessing .port also rejects malformed/non-numeric ports.
        _ = parts.port
        return str(parts.netloc or '').lower()
    except (TypeError, ValueError):
        return ''


async def _send_unexpected_host(send) -> None:
    payload=json.dumps({'detail':'HOST_NOT_ALLOWED'},separators=(',',':')).encode('utf-8')
    headers=[
        (b'content-type',b'application/json'),
        (b'cache-control',b'no-store'),
        (b'x-content-type-options',b'nosniff'),
        (b'referrer-policy',b'no-referrer'),
    ]
    await send({'type':'http.response.start','status':421,'headers':headers})
    await send({'type':'http.response.body','body':payload})


def _scope_host(scope) -> str:
    """Return one unambiguous HTTP Host authority, otherwise fail closed.

    RFC 9110 permits only one Host field. Accepting the first of multiple values can
    make Breadmaker and an upstream proxy disagree about which authority was requested,
    undermining the real-auth host pin.
    """
    values=[]
    for raw_name,raw_value in scope.get('headers') or []:
        if bytes(raw_name).lower()!=b'host':continue
        try:value=bytes(raw_value).decode('ascii').strip().lower()
        except UnicodeDecodeError:return ''
        if not value:return ''
        values.append(value)
    return values[0] if len(values)==1 else ''


def _runtime_payload() -> dict:
    """Project-free runtime metadata owned by the outer ASGI bootstrap boundary.

    The desktop launcher points Uvicorn at this module rather than directly at the
    large FastAPI application. That guarantees the first browser request cannot be
    shadowed by route registration, middleware ordering, project state, or a partially
    incompatible core application import.
    """
    return dict(request_pipeline._runtime_bootstrap_payload())


async def app(scope, receive, send):
    if scope.get('type')=='http' and scope.get('path')=='/healthz' and str(scope.get('method') or '').upper() in {'GET','HEAD'}:
        await _send_load_balancer_health(send,head=str(scope.get('method') or '').upper()=='HEAD')
        return
    if scope.get('type')=='http':
        expected_host=_expected_real_auth_host()
        if expected_host and _scope_host(scope)!=expected_host:
            await _send_unexpected_host(send)
            return
    if scope.get('type')=='http' and scope.get('path')=='/api/runtime' and str(scope.get('method') or '').upper() in {'GET','HEAD'}:
        payload=json.dumps(_runtime_payload(),separators=(',',':')).encode('utf-8')
        headers=[
            (b'content-type',b'application/json'),
            (b'cache-control',b'no-store, max-age=0'),
            (b'x-breadmaker-bootstrap',b'outer-asgi'),
        ]
        await send({'type':'http.response.start','status':200,'headers':headers})
        await send({'type':'http.response.body','body':b'' if str(scope.get('method') or '').upper()=='HEAD' else payload})
        return
    limit=_hosted_body_limit(scope)
    if limit is not None:
        await _serve_with_hosted_body_limit(scope,receive,send,limit)
        return
    await core.app(scope,receive,send)
