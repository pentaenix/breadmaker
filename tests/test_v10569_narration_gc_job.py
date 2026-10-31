"""Bounded daily job, physical census and existing staged-retention holds."""
import json
from types import SimpleNamespace

import pytest

from hosted.narration.gc import LEASE
from hosted.narration.gc_job import GoogleGCQueue, inspect_object, retention_allows, run_batch
from hosted.narration.recordings import ATTEMPTS, QUEUE, _head, _intent
from tests.test_v10569_narration_gc import collector, expired_delete, usage
from tests.test_v10569_narration_recordings import env, UID, PID, publish


class Queue:
    def __init__(self, documents): self.documents, self.calls = documents, []
    def page(self, *, after, now, limit=25):
        self.calls.append((after, now, limit))
        cursor = json.loads(after) if after else {'eligible': 0, 'id': ''}
        rows = self.documents.query_collection((QUEUE,))
        rows = sorted(rows, key=lambda row: (row.data['eligible_at'], row.path[-1]))
        return [(dict(row.data), json.dumps({'eligible': row.data['eligible_at'], 'id': row.path[-1]})) for row in rows
                if row.data['eligible_at'] <= now and (row.data['eligible_at'], row.path[-1]) > (cursor['eligible'], cursor['id'])][:limit]


def inventory(env):
    def page(*, after='', limit=100):
        rows = [row for row in env[3].list_prefix('') if row.name > after]
        return rows[:limit], rows[limit - 1].name if len(rows) > limit else ''
    env[3].inventory_page = page


def test_empty_job_is_bounded_no_polling_and_persists_only_derivative_cursor(env):
    gc = collector(env); inventory(env)
    queue = Queue(env[2])
    result = run_batch(gc, queue)
    assert result == dict(collected=0, held=0, tracked=0, fences=0, orphans=0, partial=False)
    assert len(queue.calls) == 1
    assert env[2].get(LEASE).data['holder'] == ''
    assert env[1].project_generation(PID, UID) == 4


def test_job_collects_eligible_only_then_independently_censuses_whole_bucket(env):
    made, _, _ = expired_delete(env)
    second, _ = publish(env, operation='keep_active')
    gc = collector(env); inventory(env)
    result = run_batch(gc, Queue(env[2]))
    assert result == dict(collected=1, held=0, tracked=1, fences=1, orphans=0, partial=False)
    assert env[2].get(_intent(PID, made['publication'])) is None
    assert env[2].get(_head(PID, second['id'])).data['state'] == 'active'


def test_physical_orphan_outside_namespace_is_reported_and_never_deleted(env):
    env[3].write_chunks('unknown/synthetic', [b'x'], max_bytes=1,
        content_type='application/octet-stream', if_generation_match=0)
    gc = collector(env); inventory(env)
    result = run_batch(gc, Queue(env[2]))
    assert result['orphans'] == 1 and not result['partial']
    assert env[3].stat('unknown/synthetic').size == 1


def test_missing_intent_index_or_broken_fence_is_not_silently_accepted(env):
    made, _ = publish(env)
    gc = collector(env); lease = gc.acquire()
    item = env[3].list_prefix('projects/')[0]
    assert inspect_object(gc, item, lease) == 'tracked'
    aid = item.name.split('/')[3]
    index = env[2].get((ATTEMPTS, aid)).data
    env[2].run_transaction(lambda tx: tx.set((ATTEMPTS, aid), {**index, 'fenced': True}))
    assert inspect_object(gc, item, lease) == 'orphans'
    env[2].run_transaction(lambda tx: tx.set((ATTEMPTS, aid), index))
    env[2].run_transaction(lambda tx: tx.delete(_intent(PID, made['publication'])))
    assert inspect_object(gc, item, lease) == 'orphans'


def test_retention_callback_preserves_trash_owner_drift_and_unknown_authority(env):
    now = 2_000_000_000
    allowed = lambda: env[2].run_transaction(lambda tx: retention_allows(tx, PID, UID, now=now))
    assert allowed()
    path = ('projects', PID)
    root = dict(env[2].get(path).data)
    def update(**changes):
        env[2].run_transaction(lambda tx: tx.set(path, {**root, **changes}))
    update(status='deleted', deleted_at='2033-05-18T00:00:00Z')
    assert not allowed()
    update(status='deleted', deleted_at='2026-10-01T00:00:00Z')
    assert allowed()
    update(legal_hold=True)
    assert not allowed()
    update(status='unknown')
    assert not allowed()
    update()
    env[2].run_transaction(lambda tx: tx.delete(('projects', PID, 'members', UID)))
    assert not allowed()


def test_existing_erasure_fence_is_not_reinterpreted_as_purge_approval(env):
    now = 2_000_000_000
    path, rid = ('projects', PID), 'synthetic_erasure'
    root = dict(env[2].get(path).data)
    env[2].run_transaction(lambda tx: tx.set(path, {**root, 'status': 'erasure_pending', '_bm_erasure_operation': rid}))
    env[2].run_transaction(lambda tx: tx.set(('account_erasure_locks', UID), {'request_id': rid}))
    request = dict(user_id=UID, state='closed_retention_pending', owned_project_ids=[PID],
                   retain_until='2026-10-01T00:00:00Z', physical_purge_authorized=False)
    env[2].run_transaction(lambda tx: tx.set(('account_erasures', rid), request))
    allowed = lambda: env[2].run_transaction(lambda tx: retention_allows(tx, PID, UID, now=now))
    assert not allowed()
    env[2].run_transaction(lambda tx: tx.set(('account_erasures', rid), {**request, 'physical_purge_authorized': True}))
    assert allowed()
    env[2].run_transaction(lambda tx: tx.set(('account_erasures', rid), {**request, 'physical_purge_authorized': True, 'owned_project_ids': []}))
    assert not allowed()


class Query:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def where(self, **kwargs): self.calls.append(('where', kwargs)); return self
    def order_by(self, field): self.calls.append(('order_by', field)); return self
    def start_after(self, value): self.calls.append(('start_after', value)); return self
    def limit(self, limit): self.calls.append(('limit', limit)); return self
    def stream(self, **kwargs): self.calls.append(('stream', kwargs)); return iter(self.rows)
    def document(self, key): return 'synthetic-ref/' + key


def test_real_queue_query_is_eligible_only_bounded_and_cursor_has_no_prose():
    pub = 'd' * 32
    row = SimpleNamespace(id=pub, to_dict=lambda: dict(v=1, project_id=PID,
        publication=pub, owner_user_id=UID, eligible_at=1000.0))
    query = Query([row])
    client = SimpleNamespace(collection=lambda name: query if name == QUEUE else None)
    queue = GoogleGCQueue(client)
    result = queue.page(after='', now=2000)
    assert result[0][0]['publication'] == pub
    assert ('limit', 25) in query.calls and ('stream', dict(timeout=10, retry=None)) in query.calls
    queue.page(after=result[0][1], now=2000)
    assert ('start_after', {'eligible_at': 1000.0, '__name__': 'synthetic-ref/' + pub}) in query.calls
    before = len(query.calls)
    with pytest.raises(ValueError, match='CURSOR_INVALID'):
        queue.page(after='{"eligible":0,"id":"../../private"}', now=2000)
    assert not any(name == 'stream' for name, _ in query.calls[before:])
