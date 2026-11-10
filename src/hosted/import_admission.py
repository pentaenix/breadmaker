"""Per-process import admission before multipart buffering, never job authority."""
from __future__ import annotations

import re
from threading import BoundedSemaphore


_HEAVY_PATH = re.compile(r'/api/projects/import(?:/jobs/[^/]+/(?:prepare|step))?/?\Z')
_capacity = BoundedSemaphore(1)


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
    try:
        await app(scope, receive, send)
    finally:
        _capacity.release()
