"""Per-process import admission before multipart buffering, never job authority."""
from __future__ import annotations

import re
from contextvars import ContextVar
from threading import BoundedSemaphore, Lock


_HEAVY_PATH = re.compile(r'/api/projects/import(?:/jobs/[^/]+/(?:prepare|step))?/?\Z')
_capacity = BoundedSemaphore(1)
_lease = ContextVar('hosted_import_admission', default=None)


class _Lease:
    def __init__(self, capacity):
        self.capacity = capacity
        self.lock = Lock()
        self.closed = self.released = False
        self.workers = 0

    def start_worker(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('HOSTED_IMPORT_REQUEST_CLOSED')
            self.workers += 1

    def _release_if_finished(self):
        if self.closed and not self.workers and not self.released:
            self.released = True
            self.capacity.release()

    def finish_worker(self):
        with self.lock:
            self.workers -= 1
            self._release_if_finished()

    def close(self):
        with self.lock:
            self.closed = True
            self._release_if_finished()


def run_worker(function, *args, **kwargs):
    lease = _lease.get()
    if lease is None:
        return function(*args, **kwargs)
    # Raw asyncio cancellation can outlive AnyIO's request awaiter. Keep the
    # slot until actual worker exit; a late worker cannot enter a closed lease.
    lease.start_worker()
    try:
        return function(*args, **kwargs)
    finally:
        lease.finish_worker()


def needs_admission(scope):
    return (scope.get('type') == 'http' and scope.get('method', '').upper() == 'POST'
            and _HEAVY_PATH.fullmatch(scope.get('path', '')) is not None)


async def serve(app, scope, receive, send):
    # Do not queue uploads: even a waiter can retain a large multipart spool or
    # payload. Admission precedes the first receive and all framework parsers.
    # This is a resource cap, not authentication; admitted requests still pass
    # every ordinary host/session/CSRF/idempotency/authority boundary.
    if not _capacity.acquire(blocking=False):
        await send({'type': 'http.response.start', 'status': 503, 'headers': [
            (b'content-type', b'application/json'), (b'cache-control', b'private, no-store'),
            (b'retry-after', b'2'), (b'x-content-type-options', b'nosniff')]})
        await send({'type': 'http.response.body', 'body':
            b'{"detail":{"code":"HOSTED_IMPORT_BUSY","message":"Another import request is running. Resume this import shortly."}}'})
        return
    lease = _Lease(_capacity)
    token = _lease.set(lease)
    try:
        await app(scope, receive, send)
    finally:
        _lease.reset(token)
        lease.close()
