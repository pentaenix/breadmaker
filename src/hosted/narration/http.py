"""Explicitly injected narration HTTP adapter; no local filesystem bridge.

Not installed until the complete hosted service bundle passes activation gates.
Provider, identity and project callbacks are server composition, never JSON IDs.
"""
import time
from threading import BoundedSemaphore

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

from hosted.firestore.contracts import (DocumentAlreadyExists, DocumentConflict, DocumentNotFound,
    HostedAuthorizationError, HostedValidationError, ProjectUnavailable)
from hosted.narration.reads import NarrationReads
from hosted.narration.sealed_parts import PART_BYTES
from hosted.narration.transfers import TransferDenied
from hosted.idempotency import request_scope_key

CONTROL_BYTES = 512 * 1024
TOTAL_SECONDS = 55
_CAPACITY = BoundedSemaphore(4)


class LeasedAudioResponse(Response):
    def __init__(self, body, permit, budget, *, deadline):
        super().__init__(body, media_type='application/octet-stream', headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
        self.permit, self.budget, self.deadline = permit, budget, deadline
        self.release_capacity = None

    async def __call__(self, scope, receive, send):
        try:
            remaining = min(self.deadline - time.monotonic(),
                            self.permit.expires_ms / 1000 - time.time())
            if remaining <= 0:
                raise TimeoutError('NARRATION_RESPONSE_DEADLINE')
            with anyio.fail_after(remaining):
                await super().__call__(scope, receive, send)
        finally:
            # Shield short, bounded cleanup from a disconnected client. A crashed
            # instance still has the durable 90s lease fallback; no byte refund.
            try:
                with anyio.move_on_after(5, shield=True):
                    await anyio.to_thread.run_sync(lambda: self.budget.finish(self.permit), abandon_on_cancel=True)
            finally:
                if self.release_capacity is not None:
                    self.release_capacity()


def build_recording_router(*, provider, identity, project, model_provider=None):
    """Callbacks are called within the existing authenticated request context."""
    class Boundary(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()
            async def bounded(request):
                if provider() is None:
                    raise HTTPException(404)
                if not _CAPACITY.acquire(blocking=False):
                    raise HTTPException(429, 'NARRATION_SERVER_BUSY', headers={'Retry-After': '2'})
                try:
                    return await admitted(request)
                finally:
                    if not getattr(request.state, '_narration_capacity_transferred', False):
                        _CAPACITY.release()

            async def admitted(request):
                identity()  # prove authentication BEFORE receiving/parsing a body
                if request.headers.get('x-breadmaker-action'):
                    raise HTTPException(409, 'NARRATION_NOT_A_CANONICAL_ACTION')
                limit = (PART_BYTES if request.method == 'PUT' else
                         4 * 1024**2 if request.method == 'POST' and request.url.path == '/api/narrations'
                         else CONTROL_BYTES)
                raw = request.headers.get('content-length')
                if raw is not None:
                    try:
                        length = int(raw)
                    except ValueError:
                        raise HTTPException(400, 'REQUEST_CONTENT_LENGTH_INVALID') from None
                    if length < 0:
                        raise HTTPException(400, 'REQUEST_CONTENT_LENGTH_INVALID')
                    if length > limit:
                        raise HTTPException(413, 'NARRATION_REQUEST_TOO_LARGE')
                received = 0
                async def receive():
                    nonlocal received
                    message = await request.receive()
                    if message['type'] == 'http.request':
                        received += len(message.get('body', b''))
                        if received > limit:
                            raise HTTPException(413, 'NARRATION_REQUEST_TOO_LARGE')
                    return message
                try:
                    with anyio.fail_after(TOTAL_SECONDS):
                        response = await handler(Request(request.scope, receive=receive))
                        response.headers['Cache-Control'] = 'private, no-store'
                        if isinstance(response, LeasedAudioResponse):
                            response.release_capacity = _CAPACITY.release
                            request.state._narration_capacity_transferred = True
                        return response
                except TransferDenied as exc:
                    raise HTTPException(429, exc.code, headers={'Retry-After': str(exc.retry_after)}) from None
                except (ProjectUnavailable, DocumentNotFound):
                    raise HTTPException(404, 'NARRATION_NOT_FOUND') from None
                except HostedAuthorizationError:
                    raise HTTPException(403, 'NARRATION_ACCESS_DENIED') from None
                except (DocumentConflict, DocumentAlreadyExists) as exc:
                    raise HTTPException(409, str(exc)) from None
                except HostedValidationError as exc:
                    raise HTTPException(400, str(exc)) from None
                except TimeoutError:
                    raise HTTPException(504, 'NARRATION_RESPONSE_DEADLINE') from None
            return bounded
    router = APIRouter(route_class=Boundary)

    async def json_body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'NARRATION_JSON_INVALID') from None
        if not isinstance(value, dict):
            raise HTTPException(400, 'NARRATION_JSON_INVALID')
        return value

    async def call(fn, *args, **kwargs):
        # Do not block the ASGI loop on GCS/KMS/Firestore. Timed-out threads may
        # finish an already bounded provider request, never send a late body.
        return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs), abandon_on_cancel=True)

    def context(request):
        return provider(), identity(), project(request)

    @router.get('/api/narrations')
    async def list_records(request: Request, after: str = ''):
        service, uid, pid = context(request)
        return await call(NarrationReads(service).list_page, uid, pid, after=after)

    @router.post('/api/narrations')
    async def begin_record(request: Request):
        service, uid, pid = context(request)
        data = await json_body(request)
        if set(data) != {'metadata', 'audio_bytes', 'operation'}:
            raise HTTPException(400, 'NARRATION_UPLOAD_REQUEST_INVALID')
        return await call(service.begin, uid, pid, data['metadata'],
                          audio_bytes=data['audio_bytes'], operation=data['operation'])

    @router.put('/api/narrations/uploads/{publication}/parts/{ordinal}')
    async def upload_part(publication: str, ordinal: int, request: Request):
        service, uid, pid = context(request)
        body = await request.body()
        return await call(service.upload_part, uid, pid, publication, ordinal, body)

    @router.post('/api/narrations/uploads/{publication}/finish')
    async def finish_record(publication: str, request: Request):
        service, uid, pid = context(request)
        return await call(service.finalize, uid, pid, publication)

    @router.delete('/api/narrations/{record_id}')
    async def delete_record(record_id: str, request: Request, expected_generation: int):
        service, uid, pid = context(request)
        try:
            operation = request_scope_key(request.headers.get('idempotency-key', ''), actor=uid,
                method='DELETE', path=request.url.path)
        except ValueError:
            raise HTTPException(400, 'HOSTED_IDEMPOTENCY_KEY_INVALID') from None
        return await call(service.delete, uid, pid, record_id,
                          expected_generation=expected_generation, operation=operation)

    @router.post('/api/narrations/{record_id}/restore')
    async def restore_record(record_id: str, request: Request):
        service, uid, pid = context(request)
        data = await json_body(request)
        if set(data) != {'undo_token'} or not isinstance(data['undo_token'], str):
            raise HTTPException(400, 'NARRATION_UNDO_INVALID')
        return await call(service.restore, uid, pid, record_id, data['undo_token'])

    @router.get('/api/narrations/{record_id}/manifest')
    async def audio_manifest(record_id: str, request: Request):
        service, uid, pid = context(request)
        def read():
            _, data = service.documents.run_transaction(lambda tx: service.record(tx, uid, pid, record_id))
            return {'audio_bytes': data['audio_bytes'], 'parts': data['part_count'], 'part_bytes': PART_BYTES}
        return await call(read)

    @router.get('/api/narrations/{record_id}/audio')
    async def audio_part(record_id: str, request: Request, part: int):
        deadline = time.monotonic() + TOTAL_SECONDS
        service, uid, pid = context(request)
        body, permit, budget = await call(NarrationReads(service).audio_part, uid, pid, record_id, part)
        return LeasedAudioResponse(body, permit, budget, deadline=deadline)

    def models():
        value = model_provider() if model_provider else None
        if value is None:
            raise HTTPException(409, 'NARRATION_MODELS_UNAVAILABLE')
        return value

    @router.get('/api/narration-models')
    async def model_manifest(request: Request):
        _, uid, pid = context(request)
        return await call(models().manifest, uid, pid)

    @router.get('/api/narration-models/{asset_id}')
    async def model_part(asset_id: str, request: Request, part: int):
        deadline = time.monotonic() + TOTAL_SECONDS
        _, uid, pid = context(request)
        body, permit, budget = await call(models().read_part, uid, pid, asset_id, part)
        return LeasedAudioResponse(body, permit, budget, deadline=deadline)

    return router
