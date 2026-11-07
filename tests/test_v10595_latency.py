"""Latency reductions retain current authority, integrity and publication fences."""
from contextvars import ContextVar
from types import SimpleNamespace
import hashlib
import threading

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from hosted.import_staging import package_import_staging
from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.storage.contracts import ObjectIntegrityError, ObjectMetadata
from hosted.storage.memory import MemoryObjectStore
from hosted.storage.publication import ImmutableObjectPublisher
from tests.test_v10594 import actor, fenced_replicas, OWNER


def test_import_upload_fence_batch_reads_fresh_authority_without_caching(monkeypatch):
    make, raw, objects, _ = fenced_replicas()
    operation = 'synthetic-latency-import'
    with actor():
        service = make()
        progress = service.begin(b'Synthetic paragraph 1\nSynthetic paragraph 2',
            'Synthetic.txt', 'heading1', 'Synthetic import', OWNER, operation)
        pid = service._load(OWNER, progress['job_id'])['project_id']
        calls = []
        native = raw.run_transaction
        class Counted:
            def __init__(self, tx): self.tx = tx
            def __getattr__(self, name): return getattr(self.tx, name)
            def get(self, path):
                calls.append(('get', path)); return self.tx.get(path)
            def get_many(self, paths):
                calls.append(('batch', list(paths))); return self.tx.get_many(paths)
        monkeypatch.setattr(raw, 'run_transaction', lambda fn, **kwargs: native(lambda tx: fn(Counted(tx)), **kwargs))
        key = f'projects/{pid}/synthetic-latency-object'
        def upload():
            with package_import_staging(operation, OWNER):
                return service.domain.objects.write_chunks(key, [b'safe'], max_bytes=10,
                    content_type='text/plain', if_generation_match=0)
        upload()
        assert len(calls) == 1 and calls[0][0] == 'batch'
        assert set(calls[0][1]) == {('projects', pid), ('users', OWNER),
            ('hosted_import_operations', operation), ('account_erasure_locks', OWNER)}
        user = dict(raw.get(('users', OWNER)).data)
        user['status'] = 'erasure_pending'
        native(lambda tx: tx.set(('users', OWNER), user))
        calls.clear()
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'): upload()
        assert len(calls) == 1 and calls[0][0] == 'batch'
        assert b''.join(objects.read_chunks(key, max_bytes=10)) == b'safe'
        user['status'] = 'active'
        native(lambda tx: tx.set(('users', OWNER), user))
        native(lambda tx: tx.create(('account_erasure_locks', OWNER), {'synthetic': True}))
        calls.clear()
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'): upload()
        assert len(calls) == 1 and calls[0][0] == 'batch'


def test_small_publication_uses_pinned_verified_read_and_still_checks_returned_bytes():
    class FastStore(MemoryObjectStore):
        def __init__(self): super().__init__(); self.fast = []; self.corrupt = False; self.fail = False
        def read_verified_bytes(self, name, **kwargs):
            self.fast.append((name, kwargs))
            if self.fail: raise RuntimeError('Synthetic verified read failure')
            data = b''.join(super().read_chunks(name, max_bytes=kwargs['expected_size'],
                if_generation_match=kwargs['if_generation_match']))
            return b'corrupt' if self.corrupt else data
        def read_chunks(self, *args, **kwargs): raise AssertionError('Redundant streaming metadata read')
    store = FastStore(); publisher = ImmutableObjectPublisher(store)
    payload = b'Synthetic safe manuscript'
    saved = publisher.publish_bytes('projects/synthetic/paragraph', payload, max_bytes=100)
    assert saved.object.sha256 == hashlib.sha256(payload).hexdigest()
    assert store.fast == [('projects/synthetic/paragraph', {
        'if_generation_match': saved.object.generation, 'expected_size': len(payload),
        'expected_sha256': hashlib.sha256(payload).hexdigest()})]
    replay = publisher.publish_bytes('projects/synthetic/paragraph', payload, max_bytes=100)
    assert not replay.created and replay.object == saved.object
    store.fail = True
    with pytest.raises(RuntimeError, match='Synthetic verified read failure'):
        publisher.publish_bytes('projects/synthetic/paragraph', payload, max_bytes=100)
    store.fail = False
    store.corrupt = True
    with pytest.raises(ObjectIntegrityError, match='HASH_MISMATCH'):
        publisher.publish_bytes('projects/synthetic/corrupt', payload, max_bytes=100)


def test_empty_and_large_publication_keep_bounded_streaming_verification():
    class StreamStore(MemoryObjectStore):
        def read_verified_bytes(self, *args, **kwargs): raise AssertionError('Unbounded fast read')
    for payload in (b'', b'x' * (2 * 1024 * 1024 + 1)):
        saved = ImmutableObjectPublisher(StreamStore()).publish_bytes(
            'projects/synthetic/stream', payload, max_bytes=len(payload))
        assert saved.object.size == len(payload)


def test_chapter_reads_overlap_keep_request_context_and_reject_changed_generation(monkeypatch):
    import hosted.http_api as http
    import hosted.editing_packs as packs
    context = ContextVar('synthetic_chapter_context', default=None)
    rendezvous = threading.Barrier(3)
    generation = [7, 7]
    domain = SimpleNamespace(project_generation=lambda *_: generation.pop(0))
    monkeypatch.setattr(http, '_pid', lambda *_: 'synthetic-project')
    monkeypatch.setattr(http, '_uid', lambda: 'synthetic-user')
    monkeypatch.setattr(http, '_domain', lambda: domain)
    def read(value):
        assert context.get() == 'request-owner'
        rendezvous.wait(timeout=2)
        return value
    monkeypatch.setattr(http, '_get', lambda *_: read({'id': 'chapter'}))
    monkeypatch.setattr(http, '_list', lambda *_, **kw: read([{'id': 'block', 'text_content': 'safe'}]))
    monkeypatch.setattr(packs, 'annotation_view', lambda *_, **kw: read([]))
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/chapters/chapter',
        'query_string': b'', 'headers': []})
    token = context.set('request-owner')
    try:
        result = http.hosted_chapter_get('chapter', request)
        assert result['revision'] == '7' and result['blocks'][0]['text_content'] == 'safe'
        generation[:] = [7, 8]
        with pytest.raises(HTTPException) as failure: http.hosted_chapter_get('chapter', request)
        assert failure.value.status_code == 409
        assert failure.value.detail == 'HOSTED_SNAPSHOT_GENERATION_CHANGED'
        calls = []
        def revoked(*_):
            calls.append(True)
            if len(calls) == 2: raise ProjectUnavailable('PROJECT_NOT_FOUND')
            return 7
        domain.project_generation = revoked
        with pytest.raises(ProjectUnavailable): http.hosted_chapter_get('chapter', request)
        domain.project_generation = lambda *_: generation.pop(0)
        generation[:] = [7]
        conditional = Request({'type': 'http', 'method': 'GET', 'path': '/api/chapters/chapter',
            'query_string': b'', 'headers': [(b'if-none-match', result['read_tag'].encode())]})
        assert http.hosted_chapter_get('chapter', conditional).status_code == 304
    finally: context.reset(token)
