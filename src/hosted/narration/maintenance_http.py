"""One fixed machine-only operation in the existing app, default OFF.

No browser session/admin role, caller-selected project, bucket, cursor or budget.
The outer hosted boundary delegates ONLY this exact path to this owner. A lost
HTTP response leaves the distributed lease/cursors and quota fences recoverable.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore

from fastapi.responses import JSONResponse

from hosted.firestore.contracts import DocumentConflict
from hosted.narration.maintenance_identity import PATH


class MaintenanceOperation:
    def __init__(self, identity, run):
        self.identity, self.run = identity, run
        self.capacity = BoundedSemaphore(1)
        self.workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='narration-maintenance')

    def _execute(self, token):
        try:
            if self.identity.verify(token) is not True:
                raise ValueError()
        except Exception:
            return 401, {'detail': 'NARRATION_GC_IDENTITY_REQUIRED'}
        try:
            result = self.run()
            if (set(result) != {'collected', 'held', 'tracked', 'fences', 'orphans', 'partial'}
                    or type(result['partial']) is not bool or any(type(result[key]) is not int or result[key] < 0
                        for key in result if key != 'partial')):
                raise ValueError()
            return 200, {'ok': result['orphans'] == 0, **result}
        except DocumentConflict:
            return 409, {'detail': 'NARRATION_GC_BUSY_OR_HELD'}
        except Exception:
            # No tokens, object paths, ownership, prose or provider exception body.
            return 503, {'detail': 'NARRATION_GC_FAILED_RESUMABLE'}

    async def handle(self, request):
        def reply(status, body):
            return JSONResponse(body, status_code=status, headers={'Cache-Control': 'private, no-store'})
        if (request.scope.get('path') != PATH or request.scope.get('raw_path', PATH.encode()) != PATH.encode()
                or request.scope.get('root_path')):
            return reply(404, {'detail': 'NOT_FOUND'})
        if request.method != 'POST':
            return reply(405, {'detail': 'METHOD_NOT_ALLOWED'})
        # Ordinary browser/CSRF and account/project headers cannot select or
        # authenticate maintenance. Reject them instead of silently ignoring them.
        forbidden = {'cookie', 'origin', 'x-breadmaker-project', 'x-breadmaker-user',
                     'x-breadmaker-action', 'x-breadmaker-narration-account', 'idempotency-key'}
        if request.scope.get('query_string') or any(key in request.headers for key in forbidden):
            return reply(400, {'detail': 'NARRATION_GC_REQUEST_INVALID'})
        auth = request.headers.getlist('authorization')
        lengths = request.headers.getlist('content-length')
        if (len(auth) != 1 or not auth[0].startswith('Bearer ') or len(auth[0]) > 8199):
            return reply(401, {'detail': 'NARRATION_GC_IDENTITY_REQUIRED'})
        if len(lengths) > 1 or lengths and lengths != ['0'] or 'transfer-encoding' in request.headers:
            return reply(400, {'detail': 'NARRATION_GC_REQUEST_INVALID'})
        try:
            # Read only a single ASGI message; NEVER allocate an arbitrary body.
            message = await asyncio.wait_for(request.receive(), timeout=1)
            if message.get('type') != 'http.request' or message.get('body') or message.get('more_body'):
                return reply(400, {'detail': 'NARRATION_GC_REQUEST_INVALID'})
        except Exception:
            return reply(400, {'detail': 'NARRATION_GC_REQUEST_INVALID'})
        if not self.capacity.acquire(blocking=False):
            return reply(429, {'detail': 'NARRATION_GC_BUSY'})
        try:
            future = self.workers.submit(copy_context().run, self._execute, auth[0][7:])
        except BaseException:
            self.capacity.release()
            raise
        # The actual worker owns the slot, even if HTTP is cancelled/times out.
        future.add_done_callback(lambda _: self.capacity.release())
        try:
            status, body = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=50)
            return reply(status, body)
        except TimeoutError:
            return reply(503, {'detail': 'NARRATION_GC_FAILED_RESUMABLE'})


async def dispatch(request, bundle):
    operation = getattr(bundle, 'maintenance', None)
    if operation is None:
        return JSONResponse({'detail': 'NOT_FOUND'}, status_code=404, headers={'Cache-Control': 'private, no-store'})
    return await operation.handle(request)
