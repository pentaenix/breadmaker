"""Cold-read I/O savings preserve authority, integrity and local cache safety."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response

from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.contracts import ObjectIntegrityError, ObjectGenerationConflict
from tests.test_v10554 import imported, OWNER
from tests.test_v10560 import book


def fake_google(data):
    calls=[]
    class Blob:
        def download_as_bytes(self,**kwargs):
            calls.append(kwargs)
            return data
        def reload(self,**kwargs):pytest.fail('Redundant metadata RPC')
        def open(self,*args,**kwargs):pytest.fail('Redundant streaming RPC')
    store=GoogleCloudObjectStore(bucket='synthetic',client=SimpleNamespace(
        bucket=lambda _:SimpleNamespace(blob=lambda _:Blob())))
    return store,calls


def test_single_media_read_is_generation_pinned_raw_bounded_and_hash_verified():
    data=b'Synthetic compressed bytes';store,calls=fake_google(data)
    assert store.read_verified_bytes('objects/bundle',if_generation_match=17,
        expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())==data
    assert len(calls)==1
    assert calls[0]['start']==0 and calls[0]['end']==len(data)
    assert calls[0]['if_generation_match']==17 and calls[0]['raw_download'] is True
    assert calls[0]['single_shot_download'] is True and calls[0]['timeout']==20
    for invalid in (data+b'x',data[:-1],b'x'*len(data)):
        bad,_=fake_google(invalid)
        with pytest.raises(ObjectIntegrityError):
            bad.read_verified_bytes('objects/bundle',if_generation_match=17,
                expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())


def test_invalid_verified_read_limits_reject_before_any_rpc():
    store,calls=fake_google(b'')
    for size,generation,digest in ((0,1,'a'*64),(2*1024*1024+1,1,'a'*64),
            (10,0,'a'*64),(10,1,''),(10,1,'g'*64)):
        with pytest.raises(ValueError):
            store.read_verified_bytes('objects/bundle',if_generation_match=generation,
                expected_size=size,expected_sha256=digest)
    assert not calls


def test_provider_precondition_error_cannot_be_retried_as_unpinned_read(monkeypatch):
    class Precondition(Exception):pass
    class Blob:
        def download_as_bytes(self,**kwargs):raise Precondition()
    store=GoogleCloudObjectStore(bucket='synthetic',client=SimpleNamespace(
        bucket=lambda _:SimpleNamespace(blob=lambda _:Blob())))
    monkeypatch.setattr(store,'_translate',lambda _:ObjectGenerationConflict('Synthetic generation changed'))
    with pytest.raises(ObjectGenerationConflict):
        store.read_verified_bytes('objects/bundle',if_generation_match=1,
            expected_size=10,expected_sha256='a'*64)


def install_reader(monkeypatch,one,after_read=None):
    native=one.projects.objects.read_chunks;calls=[]
    def reader(name,*,if_generation_match,expected_size,expected_sha256):
        data=b''.join(native(name,max_bytes=expected_size,if_generation_match=if_generation_match))
        assert len(data)==expected_size and hashlib.sha256(data).hexdigest()==expected_sha256
        calls.append(name)
        if after_read:after_read()
        return data
    monkeypatch.setattr(one.projects.objects,'read_verified_bytes',reader,raising=False)
    return calls


def test_fast_book_uses_one_verified_media_read_per_bundle_and_never_writes(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,_=imported(monkeypatch,count=600)
    monkeypatch.setattr(http,'runtime',one)
    before={row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}
    calls=install_reader(monkeypatch,one)
    with one.context.bind(pid,user_id=OWNER):response=book(http,one,pid)
    payload=json.loads(response.body)
    assert response.headers['Cache-Control']=='private, no-store'
    assert payload['bundled'] and len(payload['blocks'])==600
    assert [b['text_content'] for b in payload['blocks']]==[f'Synthetic paragraph {i}' for i in range(600)]
    assert len(calls)==len(set(calls))<20
    assert before=={row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}
    timings=response.headers['Server-Timing']
    assert {part.split(';')[0].strip() for part in timings.split(',')}=={'manifest','bundles','authority','encode'}
    assert all(float(part.split('dur=')[1])>=0 for part in timings.split(','))


def test_optimized_reads_still_fence_revocation_and_concurrent_edits(monkeypatch):
    import hosted.http_api as http
    for change,status in (('revoke',404),('edit',409)):
        one,_,docs,pid,_=imported(monkeypatch,count=130)
        monkeypatch.setattr(http,'runtime',one)
        fired=[]
        def race():
            if fired:return
            fired.append(True)
            if change=='revoke':docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)))
            else:one.projects.rename(pid,'Concurrent synthetic edit')
        install_reader(monkeypatch,one,race)
        with one.context.bind(pid,user_id=OWNER),pytest.raises(HTTPException) as error:book(http,one,pid)
        assert error.value.status_code==status


def test_only_packaged_get_and_head_skip_account_binding(monkeypatch):
    import hosted.http_api as http
    from auth.sessions import AuthenticationError
    monkeypatch.setattr(http,'runtime',SimpleNamespace(mode='hosted',sessions=SimpleNamespace(
        cookie_name='breadmaker-session',requires_authentication=True,
        metadata=lambda:{'auth_mode':'identity_platform'})))
    attempts=[]
    @asynccontextmanager
    async def denied(*args,**kwargs):
        attempts.append(True)
        raise AuthenticationError('Synthetic revoked cookie')
        yield
    monkeypatch.setattr(http,'_provider_binding',denied)
    async def handler(request):return Response('Public packaged asset')
    def send(path,method='GET'):
        return asyncio.run(http.hosted_request_boundary(Request({
            'type':'http','method':method,'path':path,'raw_path':b'/static/disguised',
            'headers':[(b'cookie',b'breadmaker-session=synthetic')],'query_string':b'',
            'scheme':'https','server':('example.invalid',443)}),handler))
    for method in ('GET','HEAD'):assert send('/static/css/reviews/manuscript-layer.css',method).status_code==200
    assert not attempts
    for path,method in (('/api/assets/private.png','GET'),('/api/book','GET'),
                        ('/static/style.css','POST'),('/static-private/style.css','GET')):
        assert send(path,method).status_code==401
    assert len(attempts)==4


def test_hosted_api_authority_does_not_block_static_and_is_bound_until_handler(monkeypatch):
    import hosted.http_api as http
    import threading
    from contextvars import ContextVar
    principal=ContextVar('synthetic_principal',default=None)
    project=ContextVar('synthetic_project',default=None)
    main=threading.get_ident()
    class Sessions:
        requires_authentication=True;cookie_name='synthetic_cookie'
        def current(self):return principal.get()
        def bind(self,*args):pytest.fail('Synchronous cloud session binding used')
        @asynccontextmanager
        async def bind_async(self,token):
            assert token=='synthetic_token'
            started.set();await released.wait()
            handle=principal.set(OWNER)
            try:yield OWNER
            finally:principal.reset(handle)
    class Context:
        def bind(self,*args,**kwargs):pytest.fail('Synchronous cloud project binding used')
        @asynccontextmanager
        async def bind_async(self,pid,*,user_id):
            assert user_id==principal.get()==OWNER
            handle=project.set(pid)
            try:yield pid
            finally:project.reset(handle)
    def identify(_):
        assert threading.get_ident()!=main and principal.get()==OWNER
        return OWNER
    monkeypatch.setattr(http,'runtime',SimpleNamespace(sessions=Sessions(),context=Context(),
        identity=SimpleNamespace(request_user_id=identify)))
    pid='a'*32
    def request(path):return Request({'type':'http','method':'GET','path':path,
        'headers':[(b'cookie',b'synthetic_cookie=synthetic_token'),(b'x-breadmaker-project',pid.encode())],
        'query_string':b'','scheme':'https','server':('example.invalid',443)})
    async def handler(req):
        if req.scope['path'].startswith('/api/'):
            assert principal.get()==OWNER and project.get()==pid
        return Response('Synthetic response')
    async def scenario():
        nonlocal started,released
        started,released=asyncio.Event(),asyncio.Event()
        pending=asyncio.create_task(http.hosted_request_boundary(request('/api/book'),handler))
        await asyncio.wait_for(started.wait(),1)
        static=await asyncio.wait_for(http.hosted_request_boundary(request('/static/styles.css'),handler),1)
        assert static.status_code==200 and not pending.done()
        released.set();response=await pending
        assert response.headers['Cache-Control']=='private, no-store'
        assert principal.get() is None and project.get() is None
    started=released=None
    asyncio.run(scenario())


def test_only_current_hosted_release_code_is_cacheable_and_local_stays_no_store(client,monkeypatch):
    from backend import http_boundary as boundary
    from backend.environment import APP_VERSION
    asset='/static/css/reviews/manuscript-layer.css'
    assert 'no-store' in client.get(asset+'?v='+APP_VERSION).headers['cache-control']
    monkeypatch.setattr(boundary,'runtime',SimpleNamespace(mode='hosted_dev',
        sessions=SimpleNamespace(metadata=lambda:{})))
    for method in (client.get,client.head):
        response=method(asset+'?v='+APP_VERSION)
        assert response.status_code==200
        assert response.headers['cache-control']=='public, max-age=31536000, immutable'
        assert 'pragma' not in response.headers and 'expires' not in response.headers
        assert "script-src 'self'" in response.headers['content-security-policy']
        assert response.headers['x-content-type-options']=='nosniff'
    for path in (asset,asset+'?v=obsolete',asset+'?v='+APP_VERSION+'&v='+APP_VERSION,
            asset+'?v='+APP_VERSION+'&extra=1','/static/missing.css?v='+APP_VERSION,
            '/locales/en.json?v='+APP_VERSION,'/?v='+APP_VERSION):
        assert 'no-store' in client.get(path).headers['cache-control']
