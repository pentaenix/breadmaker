"""Narration HTTP trust/resource boundary, before body parsing or cache access."""
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from backend.environment import runtime

MAX_AUDIO=32*1024*1024
MAX_REQUEST=MAX_AUDIO+2*1024*1024
MAX_CONTROL_REQUEST=256*1024


class NarrationRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def bounded(request:Request):
            if runtime.mode!='local':raise HTTPException(404)
            # Origin checks protect mutations; GETs also need a loopback Host
            # boundary so a DNS-rebound foreign origin cannot read project audio.
            try:
                authority=urlsplit('//'+request.headers.get('host',''))
                valid=(authority.hostname in {'127.0.0.1','localhost','::1'}
                       and authority.username is None and authority.password is None
                       and (authority.port is None or 1<=authority.port<=65535)
                       and not authority.path and not authority.query and not authority.fragment)
            except ValueError:valid=False
            if not valid:raise HTTPException(400,'NARRATION_LOCAL_HOST_REQUIRED')
            limit=MAX_REQUEST if request.scope['path']=='/api/narrations' and request.method=='POST' else MAX_CONTROL_REQUEST
            raw=request.headers.get('content-length')
            if raw is not None:
                try:length=int(raw)
                except ValueError:raise HTTPException(400,'REQUEST_CONTENT_LENGTH_INVALID')
                if length<0:raise HTTPException(400,'REQUEST_CONTENT_LENGTH_INVALID')
                if length>limit:raise HTTPException(413,'NARRATION_REQUEST_TOO_LARGE')
            received=0
            async def receive():
                nonlocal received
                message=await request.receive()
                if message['type']=='http.request':
                    received+=len(message.get('body',b''))
                    if received>limit:raise HTTPException(413,'NARRATION_REQUEST_TOO_LARGE')
                return message
            return await handler(Request(request.scope,receive=receive))
        return bounded
