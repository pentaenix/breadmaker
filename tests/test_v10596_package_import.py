"""Synthetic durable native import contracts; no provider credentials or novels."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.idempotency import stable_project_id
from hosted.package_import import PackageImport
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')


def source_plan(pid, count=130, *, image=False):
    rows = [dict(entity='document_tab', id='tab_import', parents={'project_id': pid},
                 data={'title': 'Imported manuscript', 'kind': 'manuscript', 'position': 0}),
            dict(entity='chapter', id='chapter_import', parents={'project_id': pid},
                 data={'title': 'Synthetic chapter', 'tab_id': 'tab_import', 'position': 0})]
    rows += [dict(entity='manuscript_block', id=f'b_import_{index}', parents={'project_id': pid},
                  data={'chapter_id': 'chapter_import', 'tab_id': 'tab_import', 'position': index,
                        'text_content': f'Synthetic paragraph {index}', 'content_html': f'<p>Synthetic paragraph {index}</p>'})
             for index in range(count)]
    return {'title': 'Synthetic package', 'records': rows, 'annotations': [],
            'assets': [dict(path='/api/assets/older-name.png', extension='.png',
                           sha256=hashlib.sha256(PNG).hexdigest(), data=PNG)] if image else []}


@pytest.fixture
def setup(monkeypatch):
    one, make, docs, original, _ = _replicas(monkeypatch)
    monkeypatch.setitem(sys.modules, 'hosted.native_package', SimpleNamespace(
        prepare_project_package=lambda raw, pid, **kwargs: source_plan(pid, count=3 if raw == b'small' else 130)))
    return one, make, docs, original


def start(one, raw=b'package', op='op_native_package'):
    service = PackageImport(one.projects)
    progress = service.begin(raw, OWNER, operation=op)
    return service, progress


def finish_steps(service, progress):
    while not progress['ready']:
        progress = service.step(OWNER, progress['job_id'], progress['next_batch'])
    return progress


def test_resumes_across_replicas_and_publishes_once(setup):
    one, make, docs, original = setup
    service, progress = start(one)
    job = progress['job_id'];pid = stable_project_id('op_native_package')
    assert progress['total_records'] == 132 and progress['total_batches'] == 3
    assert len(json.dumps(docs.get(service._path(OWNER, job)).data)) < 2048
    assert 'plans' not in docs.get(service._path(OWNER, job)).data
    assert docs.get(('projects', pid)) is None
    with pytest.raises(ProjectUnavailable): make().projects.get(pid)
    progress = service.step(OWNER, job, 0)
    assert progress['completed_records'] == 64
    staged = docs.query_collection(('projects', pid, 'blocks'))
    assert staged and all('payload_object' in row.data and '_bm_read_bundle' in row.data for row in staged)
    replica = PackageImport(make().projects)
    assert replica.step(OWNER, job, 0) == progress
    with pytest.raises(DocumentConflict, match='OUT_OF_ORDER'): replica.step(OWNER, job, 2)
    finish_steps(replica, progress)
    first = replica.finish(OWNER, job)
    assert first['phase'] == 'completed' and first['project']['id'] == pid
    assert PackageImport(make().projects).finish(OWNER, job) == first
    assert service.begin(b'package', OWNER, operation='op_native_package') == first
    assert len(make().projects.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)) == 130
    assert docs.get(('users', OWNER)).data['last_project_id'] == pid
    assert len(docs.query_collection(('projects',))) == 2
    assert one.projects.get(original)['id'] == original


def test_pin_source_identity_and_actor_before_provider_io(setup):
    one, _, docs, _ = setup
    service, progress = start(one, b'small')
    job = progress['job_id']
    with pytest.raises(DocumentConflict, match='DIFFERENT_PACKAGE'):
        service.begin(b'changed', OWNER, operation='op_native_package')
    with pytest.raises(DocumentConflict, match='DIFFERENT_PACKAGE'):
        service.begin(b'small', OWNER, operation='op_native_package', name_override='Changed')
    with pytest.raises(DocumentNotFound, match='NOT_FOUND'):
        service.status('usr_' + 'a' * 32, job)
    plan_path = service._plan_path(OWNER, job, 0)
    desc = dict(docs.get(plan_path).data)
    desc['object_name'] = 'projects/' + 'a' * 32 + '/foreign.json.gz'
    docs.run_transaction(lambda tx: tx.set(plan_path, desc))
    with pytest.raises(HostedValidationError, match='BINDING_INVALID'):
        service.step(OWNER, job, 0)
    assert docs.get(('projects', stable_project_id('op_native_package'))) is None


def test_erasure_fences_reads_steps_and_finish(setup, monkeypatch):
    one, _, docs, _ = setup
    service, progress = start(one, b'small')
    def forbidden(*args, **kwargs): raise AssertionError('Object read after account freeze')
    monkeypatch.setattr(one.projects.objects, 'read_chunks', forbidden)
    docs.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic'}))
    for operation in (lambda: service.status(OWNER, progress['job_id']),
                      lambda: service.step(OWNER, progress['job_id'], 0),
                      lambda: service.finish(OWNER, progress['job_id'])):
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'): operation()


def test_cancellation_during_upload_never_commits_metadata(setup, monkeypatch):
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    original = one.projects.objects.write_chunks
    def race(name, chunks, **kwargs):
        service.cancel(OWNER, job)
        return original(name, chunks, **kwargs)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', race)
    with pytest.raises(DocumentConflict, match='CANCELLED'): service.step(OWNER, job, 0)
    pid = stable_project_id('op_native_package')
    assert docs.get(('projects', pid)) is None
    assert docs.query_collection(('projects', pid, 'blocks')) == []
    assert service.status(OWNER, job)['phase'] == 'cancelled'
    with pytest.raises(DocumentConflict, match='CANCELLED'): service.finish(OWNER, job)


def test_atomic_cancel_finish_race_and_lost_finish_response(setup):
    one, make, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    finish_steps(service, progress)
    def run(action):
        try: return action()
        except DocumentConflict as exc: return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, (lambda: service.finish(OWNER, job),
            lambda: PackageImport(make().projects).cancel(OWNER, job))))
    progress = service.status(OWNER, job)
    pid = stable_project_id('op_native_package')
    if progress['phase'] == 'completed':
        assert 'HOSTED_IMPORT_ALREADY_PUBLISHED' in outcomes
        assert service.finish(OWNER, job)['project']['id'] == pid
        with pytest.raises(DocumentConflict, match='ALREADY_PUBLISHED'): service.cancel(OWNER, job)
    else:
        assert 'HOSTED_IMPORT_CANCELLED' in outcomes
        assert docs.get(('projects', pid)) is None
    assert len(docs.query_collection(('projects',))) in {1, 2}


def test_missing_receipt_or_expiry_cannot_publish(setup):
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    finish_steps(service, progress)
    docs.run_transaction(lambda tx: tx.delete(service._receipt_path(OWNER, job)))
    with pytest.raises(DocumentConflict, match='RECEIPT_INVALID'): service.finish(OWNER, job)
    with pytest.raises(DocumentConflict, match='EXPIRED'):
        PackageImport(one.projects, clock=lambda: 10**12).step(OWNER, job, 0)
    assert docs.get(('projects', stable_project_id('op_native_package'))) is None


def test_nested_parent_and_original_asset_name_survive(setup, monkeypatch):
    one, make, _, _ = setup
    def prepared(raw, pid, **kwargs):
        plan = source_plan(pid, count=1, image=True)
        plan['records'] += [dict(entity='review', id='review_import', parents={'project_id': pid}, data={'title': 'Review'}),
            dict(entity='review_participant', id='participant_import', parents={'project_id': pid, 'review_id': 'review_import'},
                 data={'name': 'Synthetic reader', 'review_id': 'review_import'})]
        return plan
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', prepared)
    service, progress = start(one, b'small')
    finish_steps(service, progress)
    pid = service.finish(OWNER, progress['job_id'])['project']['id']
    replica = make()
    participants = replica.projects.domain.list('review_participant', {'project_id': pid, 'review_id': 'review_import'}, user_id=OWNER)
    assert participants[0]['id'] == 'participant_import'
    with replica.context.bind(pid, user_id=OWNER):
        assert replica.assets.read_blob('/api/assets/older-name.png') == PNG


def test_pack_preserves_virtual_annotation_ids_and_ranges(setup, monkeypatch):
    one, _, _, _ = setup
    iid = 'eann_' + '1' * 24 + '_' + '2' * 24
    def prepared(raw, pid, **kwargs):
        plan = source_plan(pid, count=1)
        plan['records'].append(dict(entity='markup_label', id='label_import', parents={'project_id': pid},
                                    data={'name': 'Editing', 'color': '#123456'}))
        plan['annotations'] = [{'ids': {'project_id': pid, 'annotation_id': iid}, 'data': {
            'block_id': 'b_import_0', 'annotation_type': 'highlight', 'label_id': 'label_import',
            'body': 'editing-search/v1:adverbs', 'start_offset': 0, 'end_offset': 9,
            'selected_text': 'Synthetic', 'prefix_context': '', 'suffix_context': '', 'status': 'open'}}]
        return plan
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package', prepared)
    service, progress = start(one, b'small')
    finish_steps(service, progress)
    pid = service.finish(OWNER, progress['job_id'])['project']['id']
    from hosted.editing_packs import PackedAnnotations
    _, head, row = PackedAnnotations(one.projects.domain).get(pid, OWNER, iid)
    assert head['id'] == 'epack_' + '1' * 24 and row['selected_text'] == 'Synthetic'
    assert not row.get('stale') and row['source_generation'] == 1


def test_failed_prepare_can_resume_and_concurrent_step_is_replay_safe(setup, monkeypatch):
    one, make, _, _ = setup
    original = one.projects.objects.write_chunks
    calls = 0
    def fail(name, chunks, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1: raise RuntimeError('Synthetic interrupted upload')
        return original(name, chunks, **kwargs)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', fail)
    with pytest.raises(RuntimeError, match='interrupted'): start(one, b'small')
    service, progress = start(one, b'small');job = progress['job_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda svc: svc.step(OWNER, job, 0), [service, PackageImport(make().projects)]))
    assert all(value['ready'] for value in outcomes)
    assert service.status(OWNER, job)['completed_records'] == 5
    assert service.finish(OWNER, job)['phase'] == 'completed'


def test_http_is_global_context_and_requires_idempotency(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hosted import http_api
    one, _, _, _ = setup
    monkeypatch.setattr(http_api, 'runtime', one)
    app = FastAPI();app.include_router(http_api.platform_router);http_api.install_request_boundary(app)
    def forbidden(*args, **kwargs): raise AssertionError('Import acquired an existing project')
    monkeypatch.setattr(one.projects, 'profile_project_target', forbidden)
    with TestClient(app) as client:
        url = '/api/projects/import?resumable=true'
        files = {'file': ('synthetic.breadmaker', b'small', 'application/zip')}
        assert client.post(url, files=files).status_code == 400
        assert client.post(url, files=files, headers={'Idempotency-Key': 'synthetic', 'X-Breadmaker-Action': 'action_old'}).status_code == 400
        result = client.post(url, files=files, headers={'Idempotency-Key': 'synthetic', 'X-Breadmaker-Project': 'a' * 32})
        assert result.status_code == 200, result.text
        job = result.json()['job_id'];base = '/api/projects/import/jobs/' + job
        progress = client.post(base + '/step?batch=0')
        assert progress.status_code == 200 and progress.json()['ready']
        done = client.post(base + '/finish');assert done.status_code == 200, done.text
        assert client.get(base).json()['project'] == done.json()['project']
        assert client.post(base + '/cancel').status_code == 409
        assert client.get('/api/runtime').json()['capabilities']['project_package_import_resumable']


def test_http_work_is_bounded_off_event_loop_with_context(monkeypatch):
    import asyncio
    from contextvars import ContextVar
    from threading import Lock, get_ident
    import time
    from anyio import CapacityLimiter
    from hosted import http_api
    marker = ContextVar('synthetic_import_actor', default=None)
    active = peak = 0
    mutex = Lock()
    def work(event_thread):
        nonlocal active, peak
        assert get_ident() != event_thread and marker.get() == OWNER
        with mutex:
            active += 1;peak = max(peak, active)
        time.sleep(0.01)
        with mutex: active -= 1
        return OWNER
    async def exercise():
        token = marker.set(OWNER)
        try:
            monkeypatch.setattr(http_api, '_package_import_capacity', CapacityLimiter(2))
            return await asyncio.gather(*(http_api._package_import_call(work, get_ident()) for _ in range(6)))
        finally: marker.reset(token)
    assert asyncio.run(exercise()) == [OWNER] * 6 and peak == 2


def test_account_freeze_during_source_read_aborts_before_staging(setup, monkeypatch):
    one, _, docs, _ = setup
    service, progress = start(one, b'small')
    original = one.projects.objects.read_chunks
    def freeze(name, **kwargs):
        yield from original(name, **kwargs)
        docs.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic'}))
    monkeypatch.setattr(one.projects.objects, 'read_chunks', freeze)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        service.step(OWNER, progress['job_id'], 0)
    pid = stable_project_id('op_native_package')
    assert docs.query_collection(('projects', pid, 'blocks')) == []
    assert docs.get(('projects', pid)) is None


def test_receipt_and_rows_commit_together_after_transaction_retry(setup, monkeypatch):
    one, _, docs, _ = setup
    service, progress = start(one, b'small')
    original = docs.run_transaction
    injected = False
    def retry(fn, **kwargs):
        nonlocal injected
        if fn.__name__ == 'commit' and not injected:
            injected = True;docs.retry_next_transaction()
        return original(fn, **kwargs)
    monkeypatch.setattr(docs, 'run_transaction', retry)
    result = service.step(OWNER, progress['job_id'], 0)
    assert injected and result['ready'] and result['completed_records'] == 5
    assert docs.get(service._receipt_path(OWNER, progress['job_id'])).data['records'] == 5
    assert len(docs.query_collection(('projects', stable_project_id('op_native_package'), 'blocks'))) == 3


def test_completed_job_never_expires_into_duplicate_project(setup):
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    finish_steps(service, progress)
    result = service.finish(OWNER, job)
    later = PackageImport(one.projects, clock=lambda: 10**12)
    assert later.begin(b'small', OWNER, operation='op_native_package')['project'] == result['project']
    assert later.status(OWNER, job)['phase'] == 'completed'
    docs.run_transaction(lambda tx: tx.delete(('projects', result['project']['id'])))
    with pytest.raises(DocumentNotFound, match='PROJECT_REMOVED'):
        later.begin(b'small', OWNER, operation='op_native_package')
    assert len(docs.query_collection(('projects',))) == 1


def test_production_lifecycle_fences_allow_only_reserved_unpublished_prefix(monkeypatch):
    from hosted.account_lifecycle import AccountLifecycleDocumentStore
    from hosted.firestore.memory import MemoryDocumentStore
    from hosted.storage.memory import MemoryObjectStore
    from runtime.hosted import build_hosted_dev_runtime
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    raw = MemoryDocumentStore();fenced = AccountLifecycleDocumentStore(raw)
    one = build_hosted_dev_runtime(documents=fenced, objects=MemoryObjectStore())
    monkeypatch.setitem(sys.modules, 'hosted.native_package', SimpleNamespace(
        prepare_project_package=lambda raw, pid, **kwargs: source_plan(pid, count=2, image=True)))
    service, progress = start(one, b'small')
    finish_steps(service, progress)
    result = service.finish(OWNER, progress['job_id'])
    assert result['phase'] == 'completed'
    assert raw.get(('projects', result['project']['id'], 'members', OWNER)).data['role'] == 'owner'


def test_interrupted_prepare_replays_only_uncommitted_plan_suffix(setup, monkeypatch):
    import hosted.package_import as module
    one, _, docs, _ = setup
    # Small synthetic groups exercise many durable preparation boundaries.
    monkeypatch.setattr(module, 'BATCH_SIZE', 2)
    monkeypatch.setattr(sys.modules['hosted.native_package'], 'prepare_project_package',
        lambda raw, pid, **kwargs: source_plan(pid, count=20))
    original = one.projects.objects.write_chunks
    seen = []
    def fail(name, chunks, **kwargs):
        seen.append(name)
        if '/plans/8/' in name: raise RuntimeError('Synthetic timeout after a durable prefix')
        return original(name, chunks, **kwargs)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', fail)
    with pytest.raises(RuntimeError, match='durable prefix'): start(one, b'small')
    from hosted.idempotency import stable_entity_id
    job = stable_entity_id('package_', 'op_native_package')
    service = PackageImport(one.projects)
    assert docs.get(service._path(OWNER, job)).data['prepared_batches'] == 8
    seen.clear()
    def record(name, chunks, **kwargs):
        seen.append(name)
        return original(name, chunks, **kwargs)
    monkeypatch.setattr(one.projects.objects, 'write_chunks', record)
    progress = service.begin(b'small', OWNER, operation='op_native_package')
    assert progress['total_batches'] == 11 and progress['phase'] == 'staging'
    assert len(seen) == 3 and not any('/plans/0/' in name for name in seen)


def test_census_accounts_for_unpublished_sources_and_unknown_children(setup):
    from hosted.account_deletion import HostedAccountDeletionPlanner
    from hosted.account_sandbox import HostedSandboxAccountClosure
    from hosted.storage.memory import MemoryObjectStore
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    pid = stable_project_id('op_native_package')
    planner = HostedAccountDeletionPlanner(docs, one.projects.objects, MemoryObjectStore(), lambda: OWNER)
    before = planner.preview(OWNER)
    assert before.staged_import_projects == (pid,)
    assert before.staged_import_object_names and not before.staged_import_document_paths
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' not in before.blockers
    assert 'HOSTED_ACCOUNT_STAGED_IMPORT_RETENTION_AND_PURGE_REQUIRED' in before.blockers
    service.step(OWNER, job, 0);service.cancel(OWNER, job)
    after = planner.preview(OWNER)
    assert after.staged_import_document_paths and before.fingerprint != after.fingerprint
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        HostedSandboxAccountClosure(planner).arm(OWNER, operation_key='synthetic')
    docs.run_transaction(lambda tx: tx.create((*service._path(OWNER, job), 'future', 'unknown'), {'value': 1}))
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in planner.preview(OWNER).blockers


def test_census_rejects_forged_import_prefix_without_claiming_foreign_project(setup):
    from hosted.account_deletion import HostedAccountDeletionPlanner
    from hosted.storage.memory import MemoryObjectStore
    one, _, docs, original = setup
    service, progress = start(one, b'small')
    path = service._path(OWNER, progress['job_id'])
    bad = dict(docs.get(path).data);bad['project_id'] = original
    docs.run_transaction(lambda tx: tx.set(path, bad))
    preview = HostedAccountDeletionPlanner(docs, one.projects.objects, MemoryObjectStore(), lambda: OWNER).preview(OWNER)
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in preview.blockers
    assert original not in preview.staged_import_projects


def test_account_closure_freezes_and_retains_import_prefix_across_retry():
    from tests.test_v10563_closure import fixture, close, EMAIL, OPERATION, PROJECT
    from hosted.account_closure import StagedAccountClosure
    from hosted.idempotency import stable_entity_id
    from datetime import datetime, timezone
    def interrupted(state):
        if state == 'account_fenced': raise RuntimeError('Synthetic freeze interruption')
    world = fixture(projects=0, checkpoint=interrupted)
    op = 'op_retained_native_import';pid = stable_project_id(op);job = stable_entity_id('package_', op)
    path = PackageImport._path(world.uid, job)
    body = {'v': 1, 'job_id': job, 'actor': world.uid, 'project_id': pid, 'operation': op,
            'source_sha256': 'a' * 64, 'name_override': '', 'phase': 'cancelled', 'expires_at': 1}
    world.docs.run_transaction(lambda tx: (
        tx.create(path, body),
        tx.create(('hosted_import_operations', op), {'user_id': world.uid, 'sha256': 'a' * 64, 'name_override': ''})))
    name = f'projects/{pid}/package-imports/{job}/retained-source'
    world.live.write_chunks(name, [b'kept'], max_bytes=10, content_type='application/octet-stream', if_generation_match=0)
    confirmation = world.engine.plan(EMAIL, operation=OPERATION).confirmation
    with pytest.raises(RuntimeError, match='freeze interruption'): close(world, confirmation)
    assert world.docs.get(('account_erasure_locks', world.uid)) is not None
    world.engine = StagedAccountClosure(world.docs, world.identities, world.audit, project=PROJECT,
                                       now=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    result = close(world, confirmation)
    assert result['retained_import_prefix_count'] == 1 and result['data_erasure_complete'] is False
    request = world.docs.query_collection(('account_erasures',))[0]
    assert request.data['retained_import_project_ids'] == [pid]
    assert world.docs.get(path) is not None and world.docs.get(('projects', pid)) is None
    assert world.live.stat(name) is not None
    assert close(world, confirmation) == result


def test_expiry_commits_terminal_fence_before_restart_safe_error(setup):
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    finish_steps(service, progress)
    later = PackageImport(one.projects, clock=lambda: 10**12)
    with pytest.raises(DocumentConflict, match='^HOSTED_IMPORT_EXPIRED$'): later.status(OWNER, job)
    assert docs.get(service._path(OWNER, job)).data['phase'] == 'expired'
    # Even a replica whose wall clock lags before the deadline cannot publish.
    for replica in (service, later, PackageImport(one.projects, clock=lambda: 0)):
        with pytest.raises(DocumentConflict, match='^HOSTED_IMPORT_EXPIRED$'): replica.finish(OWNER, job)
    assert docs.get(('projects', stable_project_id('op_native_package'))) is None


def test_pre_expiry_finish_retries_against_committed_expiry(setup, monkeypatch):
    from hosted.firestore.memory import MemoryDocumentStore
    from copy import deepcopy
    one, _, docs, _ = setup
    service, progress = start(one, b'small');job = progress['job_id']
    finish_steps(service, progress)
    original = docs.run_transaction
    interrupted = False
    def optimistic_attempt(fn, **kwargs):
        nonlocal interrupted
        if fn.__name__ == 'publish' and not interrupted:
            interrupted = True
            # Execute one pre-expiry provider attempt against an isolated view.
            # Expiry then commits to the actual provider, invalidating that
            # attempt's read set. The provider retry MUST read terminal expired.
            shadow = MemoryDocumentStore()
            shadow._documents = deepcopy(docs._documents)
            shadow._next_revision = docs._next_revision
            assert shadow.run_transaction(fn)['phase'] == 'completed'
            with pytest.raises(DocumentConflict, match='^HOSTED_IMPORT_EXPIRED$'):
                PackageImport(one.projects, clock=lambda: 10**12).status(OWNER, job)
        return original(fn, **kwargs)
    monkeypatch.setattr(docs, 'run_transaction', optimistic_attempt)
    with pytest.raises(DocumentConflict, match='^HOSTED_IMPORT_EXPIRED$'): service.finish(OWNER, job)
    assert interrupted and docs.get(service._path(OWNER, job)).data['phase'] == 'expired'
    assert docs.get(('projects', stable_project_id('op_native_package'))) is None
