"""Default-off hosted Highlights HTTP composition; ordinary session/CSRF boundary."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
import json
from threading import BoundedSemaphore
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from hosted.editing_search import HostedEditingSearch
from hosted.firestore.contracts import HostedValidationError
from hosted.narration.io_limits import deadline

_CAPACITY=BoundedSemaphore(1)
_WORKER=ThreadPoolExecutor(max_workers=1,thread_name_prefix='editing-search')


async def _run(fn):
    if not _CAPACITY.acquire(blocking=False):raise HTTPException(429,'EDITING_SEARCH_BUSY')
    reset=deadline.set(time.monotonic()+48)
    try:
        future=_WORKER.submit(copy_context().run,fn)
    except BaseException:
        _CAPACITY.release();raise
    finally:deadline.reset(reset)
    future.add_done_callback(lambda _:_CAPACITY.release())
    try:return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)),timeout=50)
    except TimeoutError:raise HTTPException(503,'EDITING_SEARCH_RETRY_REQUIRED') from None


def build_router(*,domain,identity,project,enabled):
    router=APIRouter()
    def service():
        if enabled() is not True:raise HTTPException(404,'HOSTED_FEATURE_UNAVAILABLE')
        return HostedEditingSearch(domain())
    def reply(result):
        from hosted.http_api import _json_public
        return JSONResponse(_json_public(result),headers={'Cache-Control':'private, no-store'})
    @router.get('/api/annotations/editing-search')
    async def status(request:Request,tab_id:str):
        owner=service();uid,pid=identity(),project(request)
        return reply(await _run(partial(owner.status,pid,uid,tab_id)))
    @router.post('/api/annotations/editing-search')
    async def search(request:Request):
        owner=service();uid,pid=identity(),project(request)
        raw=bytearray()
        try:
            async with asyncio.timeout(3):
                async for chunk in request.stream():
                    if len(raw)+len(chunk)>2048:raise HTTPException(413,'EDITING_SEARCH_REQUEST_TOO_LARGE')
                    raw.extend(chunk)
            data=json.loads(raw)
        except (ValueError,TimeoutError):raise HTTPException(400,'EDITING_SEARCH_REQUEST_INVALID') from None
        try:return reply(await _run(partial(owner.run,pid,uid,data)))
        except HostedValidationError as exc:
            if str(exc) in {'EDITING_SEARCH_TOO_LARGE','EDITING_SEARCH_TOO_MANY_MATCHES'}:
                raise HTTPException(413,str(exc)) from None
            raise
    return router
