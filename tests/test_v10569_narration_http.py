"""In-process adapter HTTP behavior; outer production session/CSRF stays required."""
import asyncio
import time
from contextvars import ContextVar
from threading import Event

import anyio
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from hosted.narration.http import build_recording_router, LeasedAudioResponse, BoundedControlResponse, run_provider
from hosted.narration.reads import NarrationReads
from hosted.narration.transfers import TransferPolicy, TransferDenied
from tests.test_v10569_narration_recordings import env, publish, UID, PID, OTHER, wav


def client(env, *, actor=UID, enabled=True):
    app = FastAPI()
    def identity():
        if actor is None:
            raise HTTPException(401, 'AUTHENTICATION_REQUIRED')
        return actor
    app.include_router(build_recording_router(provider=lambda: env[0] if enabled else None,
        identity=identity, project=lambda request: PID))
    return TestClient(app)


def test_disabled_adapter_and_unauthenticated_call_do_not_parse_or_write(env):
    assert client(env, enabled=False).post('/api/narrations', content=b'invalid').status_code == 404
    assert client(env, actor=None).post('/api/narrations', content=b'invalid').status_code == 401
    assert not env[2].query_collection(('narration_projects_v1', PID, 'intents'))


def test_http_begin_parts_finish_read_delete_restore(env):
    http = client(env)
    result = http.post('/api/narrations', json=dict(metadata=env[4], audio_bytes=len(wav()), operation='http'))
    assert result.status_code == 200
    made = result.json()
    assert http.put(f"/api/narrations/uploads/{made['publication']}/parts/0", content=wav()).status_code == 200
    done = http.post(f"/api/narrations/uploads/{made['publication']}/finish")
    assert done.status_code == 200
    rid = done.json()['id']
    listing = http.get('/api/narrations')
    assert listing.status_code == 200 and listing.json()['records'][0]['id'] == rid
    assert listing.headers['cache-control'] == 'private, no-store'
    assert http.get(f'/api/narrations/{rid}/manifest').json()['audio_bytes'] == len(wav())
    response = http.get(f'/api/narrations/{rid}/audio?part=0')
    assert response.status_code == 200 and response.content == wav()
    usage = env[2].query_collection(('narration_transfer_usage_v1',))
    account = next(row.data for row in usage if row.path[-1].startswith('account_'))
    assert account['audio_bytes'] == len(wav()) and not account['active']
    deleted = http.delete(f'/api/narrations/{rid}?expected_generation=1', headers={'Idempotency-Key': 'delete_http'})
    assert deleted.status_code == 200
    token = deleted.json()['undo_token']
    assert http.get(f'/api/narrations/{rid}/audio?part=0').status_code == 404
    assert http.post(f'/api/narrations/{rid}/restore', json={'undo_token': token}).status_code == 200


def test_audio_is_never_a_signed_or_public_object_url(env):
    made, _ = publish(env)
    http = client(env)
    payload = http.get(f"/api/narrations/{made['id']}/manifest").json()
    assert set(payload) == {'audio_bytes', 'parts', 'part_bytes'}
    assert 'storage.googleapis.com' not in str(payload)
    assert client(env, actor=OTHER).get(f"/api/narrations/{made['id']}/audio?part=0").status_code == 404


def test_body_ceiling_enforced_before_parsing_and_structural_action_rejected(env):
    http = client(env)
    assert http.post('/api/narrations', content=b'x' * (4 * 1024**2 + 1)).status_code == 413
    assert http.post('/api/narrations', content=b'x', headers={'X-Breadmaker-Action': 'undo_other'}).status_code == 409
    assert http.post('/api/narrations', json={'user_id': OTHER}).status_code == 400
    assert not env[2].query_collection(('narration_projects_v1', PID, 'intents'))


def test_invalid_part_and_plain_whole_audio_are_rejected(env):
    made, _ = publish(env)
    http = client(env)
    assert http.get(f"/api/narrations/{made['id']}/audio").status_code == 422
    assert http.get(f"/api/narrations/{made['id']}/audio?part=1").status_code == 400
    assert http.get(f"/api/narrations/{made['id']}/audio?part=-1").status_code == 400


def test_budget_rejects_before_private_object_io(env):
    made, _ = publish(env)
    reads = NarrationReads(env[0], policy=TransferPolicy(audio_bytes=1))
    original = env[0].parts.objects.read_chunks
    def prohibited(*args, **kwargs):
        raise AssertionError('must reserve BEFORE object I/O')
    env[0].parts.objects.read_chunks = prohibited
    with pytest.raises(TransferDenied, match='MONTHLY_TRANSFER_LIMIT'):
        reads.audio_part(UID, PID, made['id'], 0)
    env[0].parts.objects.read_chunks = original


def test_deleted_or_revoked_record_cannot_escape_after_object_read(env):
    made, _ = publish(env)
    original = env[0].parts.read
    def revoke(*args, **kwargs):
        body = original(*args, **kwargs)
        env[2].run_transaction(lambda tx: tx.delete(('projects', PID, 'members', UID)))
        return body
    env[0].parts.read = revoke
    assert client(env).get(f"/api/narrations/{made['id']}/audio?part=0").status_code == 404
    rows = env[2].query_collection(('narration_transfer_usage_v1',))
    account = next(row.data for row in rows if row.path[-1].startswith('account_'))
    assert account['audio_bytes'] == len(wav()) and not account['active']


def test_asgi_backpressure_deadline_releases_lease_without_refund(env):
    made, _ = publish(env)
    body, permit, budget = NarrationReads(env[0]).audio_part(UID, PID, made['id'], 0)
    response = LeasedAudioResponse(body, permit, budget, deadline=time.monotonic() + .02)
    async def run():
        async def send(message):
            await anyio.sleep(1)
        async def receive():
            return {'type': 'http.disconnect'}
        with pytest.raises(TimeoutError):
            await response({'type': 'http', 'method': 'GET'}, receive, send)
    asyncio.run(run())
    rows = env[2].query_collection(('narration_transfer_usage_v1',))
    account = next(row.data for row in rows if row.path[-1].startswith('account_'))
    assert account['audio_bytes'] == len(wav()) and not account['active']


def test_model_or_private_source_is_not_eagerly_loaded_by_router_creation(env):
    objects = env[0].parts.objects
    def prohibited(*args, **kwargs):
        raise AssertionError('router construction must do no provider work')
    objects.read_chunks = prohibited
    app = client(env)
    assert app.get('/api/narrations').json() == {'records': [], 'next': None}


def test_list_is_thin_and_private_details_require_current_head_generation(env):
    made, _ = publish(env)
    http = client(env)
    value = http.get('/api/narrations').json()['records'][0]
    assert value['id'] == made['id'] and 'source_text' not in value and 'timings' not in value
    assert len(value['text']) <= 160
    assert http.get(f"/api/narrations/{made['id']}").status_code == 422
    assert http.get(f"/api/narrations/{made['id']}?expected_generation=2").status_code == 409
    details = http.get(f"/api/narrations/{made['id']}?expected_generation=1")
    assert details.status_code == 200 and details.json()['source_text'] == env[4]['source_text']
    assert details.headers['cache-control'] == 'private, no-store'
    assert client(env, actor=OTHER).get(f"/api/narrations/{made['id']}?expected_generation=1").status_code == 404


def test_control_json_send_deadline_also_releases_process_capacity():
    from fastapi.responses import JSONResponse
    released = []
    response = BoundedControlResponse(JSONResponse({'ok': True}, headers={'Cache-Control': 'private, no-store'}),
                                      deadline=time.monotonic() + .02)
    response.release_capacity = lambda: released.append(True)
    async def run():
        async def send(message): await anyio.sleep(1)
        with pytest.raises(TimeoutError):
            await response({'type': 'http', 'method': 'POST'}, None, send)
    asyncio.run(run())
    assert released == [True] and response.headers['cache-control'] == 'private, no-store'


def test_cancelled_http_calls_keep_provider_slots_until_actual_work_stops():
    ready = [Event() for _ in range(4)]
    finish = Event()
    def blocked(index):
        ready[index].set()
        assert finish.wait(2)
    async def run():
        tasks = [asyncio.create_task(run_provider(blocked, i)) for i in range(4)]
        try:
            deadline = time.monotonic() + 1
            while not all(event.is_set() for event in ready) and time.monotonic() < deadline:
                await asyncio.sleep(.005)
            assert all(event.is_set() for event in ready)
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with pytest.raises(HTTPException) as error:
                await run_provider(lambda: None)
            assert error.value.status_code == 429
        finally:
            finish.set()
        # The actual worker's done callback, NOT HTTP cancellation, releases it.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                assert await run_provider(lambda: 'released') == 'released'
                return
            except HTTPException:
                await asyncio.sleep(.005)
        pytest.fail('provider slots were not released')
    asyncio.run(run())


def test_bounded_worker_retains_request_authority_context_and_releases_failed_calls():
    actor = ContextVar('synthetic-actor', default=None)
    async def run():
        token = actor.set('synthetic-bound-actor')
        try:
            assert await run_provider(actor.get) == 'synthetic-bound-actor'
            def failed(): raise ValueError('Synthetic failure')
            with pytest.raises(ValueError): await run_provider(failed)
            assert await run_provider(lambda: True)
        finally:
            actor.reset(token)
    asyncio.run(run())
