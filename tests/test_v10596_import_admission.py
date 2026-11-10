"""The outer hosted boundary never buffers a second queued native import."""
import asyncio
from threading import BoundedSemaphore

import pytest
from hosted import import_admission as admission


@pytest.mark.parametrize('path', ['/api/projects/import', '/api/projects/import/',
    '/api/projects/import/jobs/job/prepare', '/api/projects/import/jobs/job/step'])
def test_heavy_routes(path):
    assert admission.needs_admission({'type': 'http', 'method': 'POST', 'path': path})


@pytest.mark.parametrize('path,method', [('/api/projects/import/jobs/job','GET'),
    ('/api/projects/import/jobs/job/cancel','POST'), ('/api/projects/import/jobs/job/finish','POST'),
    ('/api/projects/imported','POST'), ('/api/projects/import','GET'), ('/api/assets','POST')])
def test_control_and_unrelated_routes_bypass_admission(path, method):
    assert not admission.needs_admission({'type': 'http', 'method': method, 'path': path})


def test_rejects_concurrent_upload_before_reading_any_body_and_releases(monkeypatch):
    monkeypatch.setattr(admission, '_capacity', BoundedSemaphore(1))
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        async def receive():
            raise AssertionError('Rejected request must not read or spool body')
        async def send(message):
            pass
        async def held(scope, receive, send):
            entered.set()
            await release.wait()
        first = asyncio.create_task(admission.serve(held, {}, receive, send))
        await entered.wait()
        replies = []
        async def rejected_send(message): replies.append(message)
        async def forbidden(*args): raise AssertionError('Rejected request entered parser')
        # Many clients cannot accumulate body buffers inside an admission queue.
        await asyncio.gather(*(admission.serve(forbidden, {}, receive, rejected_send) for _ in range(10)))
        assert len([r for r in replies if r.get('status') == 503]) == 10
        release.set()
        await first
        calls = []
        async def next_request(*args): calls.append(True)
        await admission.serve(next_request, {}, receive, send)
        assert calls == [True]
    asyncio.run(check())


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
def test_failed_or_cancelled_request_releases_slot(monkeypatch, failure):
    monkeypatch.setattr(admission, '_capacity', BoundedSemaphore(1))
    async def check():
        async def broken(*args): raise failure()
        with pytest.raises(failure):
            await admission.serve(broken, {}, None, None)
        assert admission._capacity.acquire(blocking=False)
        admission._capacity.release()
    asyncio.run(check())


def test_actual_outer_boundary_admits_before_body_and_keeps_controls_live(monkeypatch):
    import src.server as server
    from tests.test_v9611 import _limits
    monkeypatch.setattr(server.environment, '_HOSTED_LIMITS', _limits())
    monkeypatch.setattr(server, '_expected_real_auth_host', lambda: '')
    monkeypatch.setattr(admission, '_capacity', BoundedSemaphore(1))
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, rejected = [], []
        async def fake(scope, receive, send):
            calls.append(scope['path'])
            if scope['path'] == '/api/projects/import':
                entered.set()
                await release.wait()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        monkeypatch.setattr(server.core, 'app', fake)
        async def receive(): raise AssertionError('No body should be read')
        async def send(message): pass
        async def collect(message): rejected.append(message)
        scope = {'type':'http','method':'POST','path':'/api/projects/import','headers':[]}
        first = asyncio.create_task(server.app(scope, receive, send))
        await entered.wait()
        try:
            await server.app(scope, receive, collect)
            for action, method in [('', 'GET'), ('/cancel','POST'), ('/finish','POST')]:
                await server.app({**scope,'method':method,'path':'/api/projects/import/jobs/job'+action},receive,send)
            assert len(calls) == 4
            assert [r['status'] for r in rejected if 'status' in r] == [503]
        finally:
            release.set()
            await first
    asyncio.run(check())


def test_cancel_scope_does_not_release_admission_while_import_thread_runs(monkeypatch):
    import anyio
    from threading import Event
    from hosted.http_api import _package_import_call
    monkeypatch.setattr(admission, '_capacity', BoundedSemaphore(1))
    async def check():
        entered, release = Event(), Event()
        scope_ready = anyio.Event()
        scopes = []
        def work():
            entered.set()
            assert release.wait(3)
        async def downstream(*args):
            await _package_import_call(work)
        async def request():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                scope_ready.set()
                await admission.serve(downstream, {}, None, None)
        async with anyio.create_task_group() as group:
            group.start_soon(request)
            await scope_ready.wait()
            while not entered.is_set():
                await anyio.sleep(0)
            try:
                scopes[0].cancel()
                await anyio.sleep(0)
                assert not admission._capacity.acquire(blocking=False)
            finally:
                release.set()
        assert admission._capacity.acquire(blocking=False)
        admission._capacity.release()
    anyio.run(check)
