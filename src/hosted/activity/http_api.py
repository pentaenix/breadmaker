"""Same-origin, server-account-bound foreground pulse. No event/counter input."""
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from .records import enabled
from .journal import pulse


def router_for(runtime):
    router = APIRouter()

    @router.post('/api/account/activity-pulse')
    async def foreground(request: Request):
        if (runtime.mode != 'hosted' or not enabled()
                or runtime.sessions.metadata().get('auth_mode') != 'identity_platform'):
            raise HTTPException(404, 'NOT_FOUND')
        if request.query_params:
            raise HTTPException(400, 'ACTIVITY_PULSE_INVALID')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2:
                raise HTTPException(400, 'ACTIVITY_PULSE_INVALID')
        if body != b'{}':
            raise HTTPException(400, 'ACTIVITY_PULSE_INVALID')
        uid = runtime.identity.request_user_id()
        # This is personal state, never a structural Undo action.
        accepted = await run_in_threadpool(pulse, runtime.identity.documents.store, uid)
        return {'accepted': accepted}

    return router
