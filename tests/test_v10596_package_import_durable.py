"""Durable continuation contracts using tiny synthetic inputs, never providers."""
import gzip
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from types import SimpleNamespace

import pytest

from hosted import package_import as imports
from hosted import package_import_snapshot as codec
from hosted.account_deletion import HostedAccountDeletionPlanner
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.idempotency import stable_entity_id, stable_project_id
from hosted.package_import import PackageImport
from hosted.storage.contracts import ObjectGenerationConflict
from hosted.storage.memory import MemoryObjectStore
from tests.test_v10596_package_import import OWNER, setup, source_plan, finish_steps


OP = 'op_durable_synthetic'
RAW = b'only synthetic package bytes'


def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected parser or provider operation')


def parser(monkeypatch, count=17):
    calls = []
    def parse(raw, pid, **kwargs):
        calls.append(raw)
        return source_plan(pid, count=count)
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', parse)
    monkeypatch.setattr(imports, 'BATCH_SIZE', 1)
    return calls


def writes(monkeypatch, objects):
    seen = []
    native = objects.write_chunks
    def record(name, chunks, **kwargs):
        seen.append(name)
        return native(name, chunks, **kwargs)
    monkeypatch.setattr(objects, 'write_chunks', record)
    return seen


def prepare_all(service, progress):
    for _ in range(50):
        if progress['phase'] != 'preparing':
            return progress
        before = progress['prepared_batches']
        progress = service.prepare(OWNER, progress['job_id'])
        assert 0 <= progress['prepared_batches'] - before <= 8
    pytest.fail('Preparation failed to terminate')


def test_single_parse_across_replica_restart_and_lost_begin_response(setup, monkeypatch):
    one, make, docs, _ = setup
    calls = parser(monkeypatch)
    seen = writes(monkeypatch, one.projects.objects)
    service = PackageImport(one.projects)
    progress = service.begin(RAW, OWNER, operation=OP, durable=True)
    job = progress['job_id']
    assert progress['phase'] == 'preparing' and progress['prepared_batches'] == 0
    assert progress['preparation_version'] == 3 and progress['prepare_available'] is True
    assert calls == [RAW]
    prior_writes = list(seen)
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    replica = PackageImport(make().projects)
    assert replica.begin(RAW, OWNER, operation=OP, durable=True) == progress
    assert seen == prior_writes
    progress = replica.prepare(OWNER, job)
    assert progress['prepared_batches'] == 8
    progress = prepare_all(PackageImport(make().projects), progress)
    assert progress['prepared_batches'] == 19
    for index in range(19):
        part = docs.get(service._plan_path(OWNER, job, index)).data
        raw = b''.join(one.projects.objects.read_chunks(part['object_name'], max_bytes=part['object_size'],
                         if_generation_match=part['object_generation']))
        value = json.loads(gzip.decompress(raw))
        assert value['v'] == 1 and value['index'] == index and len(value['items']) == 1
        assert set(value) == {'v', 'job_id', 'actor', 'project_id', 'source_sha256', 'operation', 'index', 'items'}
    finish_steps(replica, progress)
    completed = replica.finish(OWNER, job)
    assert completed['phase'] == 'completed'
    assert replica.begin(RAW, OWNER, operation=OP, durable=True) == completed


@pytest.mark.parametrize('changes', [{'raw': b'changed'}, {'name_override': 'Different title'}])
def test_same_key_changed_source_or_name_fails_before_parser(setup, monkeypatch, changes):
    one, _, _, _ = setup
    service = PackageImport(one.projects)
    service.begin(RAW, OWNER, operation=OP, durable=True)
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    args = {'raw': RAW, 'operation': OP, 'durable': True, **changes}
    with pytest.raises(DocumentConflict, match='DIFFERENT_PACKAGE'):
        service.begin(uid=OWNER, **args)


def test_invalid_input_never_reserves_operation_or_writes_objects(setup, monkeypatch):
    one, _, docs, _ = setup
    def invalid(*args, **kwargs):
        raise HostedValidationError('SYNTHETIC_INVALID_ARCHIVE')
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', invalid)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', forbidden)
    service = PackageImport(one.projects)
    with pytest.raises(HostedValidationError, match='INVALID_ARCHIVE'):
        service.begin(RAW, OWNER, operation=OP, durable=True)
    assert docs.get(('hosted_import_operations', OP)) is None
    assert docs.get(service._path(OWNER, stable_entity_id('package_', OP))) is None


@pytest.mark.parametrize('fence,match', [('cancel', 'CANCELLED'), ('expiry', 'EXPIRED'), ('erase', 'ERASURE_PENDING')])
def test_terminal_fences_block_snapshot_reads_and_plan_metadata(setup, monkeypatch, fence, match):
    one, _, docs, _ = setup
    service = PackageImport(one.projects)
    progress = service.begin(RAW, OWNER, operation=OP, durable=True)
    job = progress['job_id']
    if fence == 'cancel':
        service.cancel(OWNER, job)
    elif fence == 'expiry':
        service = PackageImport(one.projects, clock=lambda: 10**12)
    else:
        docs.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic'}))
    monkeypatch.setattr(one.projects.objects, 'read_chunks', forbidden)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', forbidden)
    with pytest.raises(DocumentConflict, match=match):
        service.prepare(OWNER, job)
    assert docs.query_collection((*service._path(OWNER, job), 'plans')) == []
    assert docs.get(('projects', stable_project_id(OP))) is None


@pytest.mark.parametrize('fence,match', [('cancel', 'CANCELLED'), ('erase', 'ERASURE_PENDING')])
def test_fence_during_snapshot_read_prevents_metadata_commit(setup, monkeypatch, fence, match):
    one, _, docs, _ = setup
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    native = one.projects.objects.read_chunks
    def race(name, **kwargs):
        result = native(name, **kwargs)
        if '/snapshots/' in name:
            if fence == 'cancel':
                service.cancel(OWNER, job)
            else:
                docs.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic'}))
        return result
    monkeypatch.setattr(one.projects.objects, 'read_chunks', race)
    with pytest.raises(DocumentConflict, match=match):
        service.prepare(OWNER, job)
    assert docs.query_collection((*service._path(OWNER, job), 'plans')) == []


def test_interrupted_snapshot_capture_replays_immutable_object(setup, monkeypatch):
    one, make, docs, _ = setup
    service = PackageImport(one.projects)
    native = one.projects.objects.write_chunks
    saved = []
    def interrupted(name, chunks, **kwargs):
        result = native(name, chunks, **kwargs)
        if '/snapshots/' in name:
            saved.append(result)
            raise RuntimeError('synthetic lost snapshot response')
        return result
    monkeypatch.setattr(one.projects.objects, 'write_chunks', interrupted)
    with pytest.raises(RuntimeError, match='lost snapshot'):
        service.begin(RAW, OWNER, operation=OP, durable=True)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', native)
    replica = PackageImport(make().projects)
    progress = replica.begin(RAW, OWNER, operation=OP, durable=True)
    descriptor = docs.get(service._snapshot_path(OWNER, progress['job_id'], 0)).data
    assert descriptor['object_generation'] == saved[0].generation
    assert descriptor['object_name'] == saved[0].name
    assert prepare_all(replica, progress)['phase'] == 'staging'


def test_oversized_capture_retains_original_once_and_resumes_without_file(setup, monkeypatch):
    one, make, docs, _ = setup
    calls = parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    seen = writes(monkeypatch, one.projects.objects)
    service = PackageImport(one.projects)
    progress = service.begin(RAW, OWNER, operation=OP, durable=True)
    job = progress['job_id']
    data = docs.get(service._path(OWNER, job)).data
    assert data['source_ready'] and not data['snapshot_ready']
    assert data['captured_chunks'] == 7 and data['total_source_chunks'] == 10
    assert len(seen) == 8
    replica = PackageImport(make().projects)
    assert replica.begin(RAW, OWNER, operation=OP, durable=True) == progress
    assert calls == [RAW] and len(seen) == 8
    progress = replica.prepare(OWNER, job)
    assert calls == [RAW, RAW]
    assert docs.get(service._path(OWNER, job)).data['snapshot_ready']
    assert len([name for name in seen if '/sources/' in name]) == 1
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    assert prepare_all(PackageImport(make().projects), progress)['phase'] == 'staging'


@pytest.mark.parametrize('mutation', ['pointer', 'version', 'bytes', 'generation'])
def test_corrupt_snapshot_or_descriptor_fails_closed(setup, monkeypatch, mutation):
    one, _, docs, _ = setup
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    path = service._snapshot_path(OWNER, job, 0)
    part = dict(docs.get(path).data)
    if mutation == 'bytes':
        native = one.projects.objects.read_chunks
        monkeypatch.setattr(one.projects.objects, 'read_chunks', lambda name, **kwargs:
            (b'corrupt',) if name == part['object_name'] else native(name, **kwargs))
    else:
        field, value = {'pointer': ('object_name', 'projects/foreign/snapshot.json.gz'),
                        'version': ('v', 2), 'generation': ('object_generation', 999)}[mutation]
        part[field] = value
        docs.run_transaction(lambda tx: tx.set(path, part))
    with pytest.raises(HostedValidationError, match='SNAPSHOT_'):
        service.prepare(OWNER, job)
    assert docs.query_collection((*service._path(OWNER, job), 'plans')) == []


def test_snapshot_generation_is_pinned_even_with_valid_descriptor_hash(setup):
    one, _, docs, _ = setup
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    path = service._snapshot_path(OWNER, job, 0)
    part = dict(docs.get(path).data)
    part['object_generation'] += 100
    part['chain'] = imports._hash({key: value for key, value in part.items() if key != 'chain'})
    docs.run_transaction(lambda tx: tx.set(path, part))
    with pytest.raises(ObjectGenerationConflict, match='GENERATION_CONFLICT'):
        service.prepare(OWNER, job)
    assert docs.query_collection((*service._path(OWNER, job), 'plans')) == []


@pytest.mark.parametrize('mutation', ['bytes', 'binding', 'changed_parser'])
def test_retained_original_is_verified_and_plan_identity_cannot_drift(setup, monkeypatch, mutation):
    one, _, docs, _ = setup
    parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    if mutation == 'bytes':
        native = one.projects.objects.read_chunks
        monkeypatch.setattr(one.projects.objects, 'read_chunks', lambda name, **kwargs:
            (b'tampered original',) if '/sources/' in name else native(name, **kwargs))
        expected, match = HostedValidationError, 'INTEGRITY_FAILED'
    elif mutation == 'binding':
        path = service._source_path(OWNER, job)
        part = dict(docs.get(path).data)
        part['actor'] = 'foreign_actor'
        docs.run_transaction(lambda tx: tx.set(path, part))
        monkeypatch.setattr(one.projects.objects, 'read_chunks', forbidden)
        expected, match = HostedValidationError, 'BINDING_INVALID'
    else:
        parser(monkeypatch, count=9)
        expected, match = DocumentConflict, 'PLAN_CHANGED'
    with pytest.raises(expected, match=match):
        service.prepare(OWNER, job)
    assert docs.get(service._path(OWNER, job)).data['captured_chunks'] == 7
    assert docs.query_collection((*service._path(OWNER, job), 'plans')) == []


def test_cancel_during_snapshot_publication_never_commits_capture_metadata(setup, monkeypatch):
    one, _, docs, _ = setup
    service = PackageImport(one.projects)
    job = stable_entity_id('package_', OP)
    native = one.projects.objects.write_chunks
    def cancel(name, chunks, **kwargs):
        result = native(name, chunks, **kwargs)
        service.cancel(OWNER, job)
        return result
    monkeypatch.setattr(one.projects.objects, 'write_chunks', cancel)
    with pytest.raises(DocumentConflict, match='CANCELLED'):
        service.begin(RAW, OWNER, operation=OP, durable=True)
    assert docs.get(service._path(OWNER, job)).data['captured_chunks'] == 0
    assert docs.query_collection((*service._path(OWNER, job), 'snapshots')) == []
    assert docs.get(('projects', stable_project_id(OP))) is None


def test_legacy_preparing_job_upgrade_preserves_committed_prefix(setup, monkeypatch):
    one, make, docs, _ = setup
    parser(monkeypatch)
    service = PackageImport(one.projects)
    progress = service.begin(RAW, OWNER, operation=OP)
    job = progress['job_id']
    assert progress['prepared_batches'] == 8 and progress['phase'] == 'preparing'
    prefix = [dict(docs.get(service._plan_path(OWNER, job, i)).data) for i in range(8)]
    progress = PackageImport(make().projects).begin(RAW, OWNER, operation=OP, durable=True)
    assert progress['prepared_batches'] == 8 and progress['prepare_available']
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    assert prepare_all(service, progress)['phase'] == 'staging'
    assert prefix == [dict(docs.get(service._plan_path(OWNER, job, i)).data) for i in range(8)]


def test_legacy_begin_advance_recovers_stale_snapshot_cursor(setup, monkeypatch):
    one, _, docs, _ = setup
    parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    service = PackageImport(one.projects)
    progress = service.begin(RAW, OWNER, operation=OP, durable=True)
    job = progress['job_id']
    service.prepare(OWNER, job)  # Finish capture without preparing source plans.
    progress = service.begin(RAW, OWNER, operation=OP)
    assert progress['prepared_batches'] == 8
    assert docs.get(service._path(OWNER, job)).data['source_chunk_cursor'] == 0
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    assert prepare_all(service, progress)['phase'] == 'staging'


@pytest.mark.parametrize('terminal', ['cancel', 'expiry'])
def test_erasure_census_retains_and_classifies_sources_snapshots_and_unknowns(setup, monkeypatch, terminal):
    one, _, docs, _ = setup
    parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    monkeypatch.setattr(one.projects.objects, 'delete', forbidden)
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    if terminal == 'cancel':
        service.cancel(OWNER, job)
    else:
        with pytest.raises(DocumentConflict, match='EXPIRED'):
            PackageImport(one.projects, clock=lambda: 10**12).prepare(OWNER, job)
    planner = HostedAccountDeletionPlanner(docs, one.projects.objects, MemoryObjectStore(), lambda: OWNER)
    preview = planner.preview(OWNER)
    assert stable_project_id(OP) in preview.staged_import_projects
    assert len([name for name in preview.staged_import_object_names if '/sources/' in name]) == 1
    assert len([name for name in preview.staged_import_object_names if '/snapshots/' in name]) == 7
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' not in preview.blockers
    assert 'HOSTED_ACCOUNT_STAGED_IMPORT_RETENTION_AND_PURGE_REQUIRED' in preview.blockers
    docs.run_transaction(lambda tx: tx.create((*service._path(OWNER, job), 'future', 'unknown'), {'v': 1}))
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in planner.preview(OWNER).blockers


@pytest.mark.parametrize('paused_kind', ['legacy', 'durable'])
def test_legacy_begin_overlaps_prepare_at_snapshot_boundary(setup, monkeypatch, paused_kind):
    one, make, docs, _ = setup
    parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    service = PackageImport(one.projects)
    job = service.begin(RAW, OWNER, operation=OP, durable=True)['job_id']
    service.prepare(OWNER, job)  # Finish ten one-batch snapshots, cursor still zero.
    published, release = Event(), Event()
    native = imports._parallel
    def pause(fn, items, **kwargs):
        result = native(fn, items, **kwargs)
        if current_thread().name.startswith('paused-import'):
            published.set()
            assert release.wait(5), 'Timed out releasing overlapping preparation'
        return result
    monkeypatch.setattr(imports, '_parallel', pause)
    replica = PackageImport(make().projects)
    actions = {'legacy': lambda: replica.begin(RAW, OWNER, operation=OP),
               'durable': lambda: replica.prepare(OWNER, job)}
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='paused-import') as pool:
        pending = pool.submit(actions[paused_kind])
        try:
            assert published.wait(5), 'Overlapping preparation never reached publication'
            actions['durable' if paused_kind == 'legacy' else 'legacy']()
            prefix = [dict(row.data) for row in docs.query_collection((*service._path(OWNER, job), 'plans'))]
        finally:
            release.set()
        pending.result(timeout=5)
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    progress = prepare_all(service, service.status(OWNER, job))
    for part in prefix:
        assert dict(docs.get(service._plan_path(OWNER, job, part['index'])).data) == part
    chain = imports._EMPTY_CHAIN
    for index in range(progress['total_batches']):
        part = dict(docs.get(service._plan_path(OWNER, job, index)).data)
        assert part['previous_chain'] == chain
        chain = imports._hash({key: value for key, value in part.items() if key != 'chain'})
        assert part['chain'] == chain
    assert docs.get(service._path(OWNER, job)).data['expected_chain'] == chain
    finish_steps(service, progress)
    receipt = docs.get(service._receipt_path(OWNER, job)).data
    assert receipt['chain'] == chain and receipt['next_batch'] == progress['total_batches']
    assert service.finish(OWNER, job)['phase'] == 'completed'


def test_overlapping_source_captures_keep_identical_group_boundary(setup, monkeypatch):
    one, make, docs, _ = setup
    calls = parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    seen = writes(monkeypatch, one.projects.objects)
    published, release = Event(), Event()
    native = imports._parallel
    def pause(fn, items, **kwargs):
        result = native(fn, items, **kwargs)
        if current_thread().name.startswith('paused-capture'):
            published.set()
            assert release.wait(5), 'Timed out releasing overlapping capture'
        return result
    monkeypatch.setattr(imports, '_parallel', pause)
    service = PackageImport(one.projects)
    job = stable_entity_id('package_', OP)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='paused-capture') as pool:
        pending = pool.submit(service.begin, RAW, OWNER, operation=OP, durable=True)
        try:
            assert published.wait(5), 'First capture never reached snapshot publication'
            state = docs.get(service._path(OWNER, job)).data
            assert state['source_ready'] and state['captured_chunks'] == 0
            second = PackageImport(make().projects).prepare(OWNER, job)
            assert second['prepared_source_chunks'] == 7
            prefix = [dict(docs.get(service._snapshot_path(OWNER, job, index)).data) for index in range(7)]
        finally:
            release.set()
        first = pending.result(timeout=5)
    assert first == second and calls == [RAW, RAW]
    assert len([name for name in seen if '/sources/' in name]) == 1
    assert len(docs.query_collection((*service._path(OWNER, job), 'snapshots'))) == 7
    progress = service.prepare(OWNER, job)
    assert docs.get(service._path(OWNER, job)).data['snapshot_ready']
    assert prefix == [dict(docs.get(service._snapshot_path(OWNER, job, index)).data) for index in range(7)]
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', forbidden)
    assert prepare_all(service, progress)['phase'] == 'staging'


@pytest.mark.parametrize('outcome', ['success', 'corrupt', 'failure', 'revoked'])
def test_retained_fast_read_pins_integrity_and_keeps_authority_guards(outcome):
    body = b'synthetic retained snapshot'
    part = {'object_name': 'synthetic-snapshot', 'object_generation': 7,
            'object_size': len(body), 'object_sha256': hashlib.sha256(body).hexdigest()}
    events = []
    permitted = True
    def check():
        events.append('guard')
        if not permitted:
            raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
    def fast(name, **kwargs):
        nonlocal permitted
        events.append(('fast', name, kwargs))
        if outcome == 'failure':
            raise RuntimeError('synthetic fast-read integrity failure')
        if outcome == 'revoked':
            permitted = False
        return b'corrupt' if outcome == 'corrupt' else body
    guarded = imports._GuardedObjects(SimpleNamespace(read_verified_bytes=fast, read_chunks=forbidden), check)
    service = object.__new__(PackageImport)
    service._objects = lambda *args: guarded
    if outcome == 'success':
        assert service._read_retained(OWNER, 'job', {}, part) == body
    else:
        expected = {'corrupt': HostedValidationError, 'failure': RuntimeError, 'revoked': DocumentConflict}[outcome]
        with pytest.raises(expected, match='INTEGRITY_FAILED|integrity failure|ERASURE_PENDING'):
            service._read_retained(OWNER, 'job', {}, part)
    assert events[:2] == ['guard', ('fast', part['object_name'], {
        'if_generation_match': 7, 'expected_size': len(body), 'expected_sha256': part['object_sha256']})]
    assert events[2:] == ([] if outcome == 'failure' else ['guard'])
    if outcome == 'revoked':
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            service._read_retained(OWNER, 'job', {}, part)
        assert len([event for event in events if isinstance(event, tuple)]) == 1


def test_large_retained_read_uses_bounded_four_mib_stream_and_rejects_short_body():
    # Claim a large pinned size without allocating a large fixture. A short
    # provider response must select streaming, then fail complete-size checking.
    part = {'object_name': 'synthetic-large-snapshot', 'object_generation': 9,
            'object_size': 2 * 1024**2 + 1, 'object_sha256': hashlib.sha256(b'short').hexdigest()}
    events = []
    def stream(name, **kwargs):
        events.append(('stream', name, kwargs))
        return [b'short']
    guarded = imports._GuardedObjects(SimpleNamespace(read_verified_bytes=forbidden, read_chunks=stream),
                                     lambda: events.append('guard'))
    service = object.__new__(PackageImport)
    service._objects = lambda *args: guarded
    with pytest.raises(HostedValidationError, match='INTEGRITY_FAILED'):
        service._read_retained(OWNER, 'job', {}, part)
    assert events == ['guard', ('stream', part['object_name'], {
        'max_bytes': part['object_size'], 'if_generation_match': 9, 'chunk_size': 4 * 1024**2}), 'guard']


def test_source_metadata_race_returns_already_completed_snapshot_capture(setup, monkeypatch):
    one, make, docs, _ = setup
    parser(monkeypatch, count=8)
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 1)
    published, release = Event(), Event()
    native = docs.run_transaction
    def pause(fn, **kwargs):
        if fn.__name__ == 'store_source' and current_thread().name.startswith('paused-source-metadata'):
            published.set()
            assert release.wait(5), 'Timed out releasing retained-source metadata transaction'
        return native(fn, **kwargs)
    monkeypatch.setattr(docs, 'run_transaction', pause)
    service = PackageImport(one.projects)
    job = stable_entity_id('package_', OP)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='paused-source-metadata') as pool:
        pending = pool.submit(service.begin, RAW, OWNER, operation=OP, durable=True)
        try:
            assert published.wait(5), 'First begin did not reach retained-source metadata transaction'
            assert docs.get(service._source_path(OWNER, job)) is None
            assert not docs.get(service._path(OWNER, job)).data['source_ready']
            replica = PackageImport(make().projects)
            replica.begin(RAW, OWNER, operation=OP, durable=True)
            completed_capture = replica.prepare(OWNER, job)
            assert docs.get(service._path(OWNER, job)).data['snapshot_ready']
            assert completed_capture['prepared_source_chunks'] == 10
            prefix = [dict(docs.get(service._snapshot_path(OWNER, job, index)).data) for index in range(10)]
        finally:
            release.set()
        replay = pending.result(timeout=5)
    assert replay == completed_capture
    assert prefix == [dict(docs.get(service._snapshot_path(OWNER, job, index)).data) for index in range(10)]
    assert docs.get(service._path(OWNER, job)).data['prepared_batches'] == 0
