"""Inactive adoption and provider-page contracts with synthetic, scoped data."""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.firestore.google_driver import _GoogleTransaction
from hosted.firestore.memory import MemoryDocumentStore
from hosted.manuscript_adoption import LegacyManuscriptAdoption, PAGE_SIZE
from hosted.manuscript_visibility import FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD, ManuscriptDocumentStore
from tests.test_v10596_bounded_manuscript_reads import TracedStore

PID, OTHER, UID, EDITOR = '1' * 32, '2' * 32, 'usr_' + '3' * 32, 'usr_' + '4' * 32
ROOT = ('projects', PID)
BLOCKS = ROOT + ('blocks',)


def world(*, chapters=2, blocks=5, scenes=2, assets=2):
    raw = MemoryDocumentStore()
    values = {
        ROOT: {'status': 'active', 'generation': 7},
        ('users', UID): {'status': 'active'}, ('users', EDITOR): {'status': 'active'},
        ROOT + ('members', UID): {'user_id': UID, 'role': 'owner'},
        ROOT + ('members', EDITOR): {'user_id': EDITOR, 'role': 'editor'},
        ROOT + ('tabs', 'tab_main'): {'tab_id': 'tab_main'},
        ROOT + ('tabs', 'tab_other'): {'tab_id': 'tab_other'},
    }
    for index in range(chapters):
        values[ROOT + ('chapters', f'ch_{index:04d}')] = {'tab_id': 'tab_main'}
    for index in range(blocks):
        values[BLOCKS + (f'b_{index:04d}',)] = {'chapter_id': f'ch_{index % chapters:04d}'}
    for index in range(scenes):
        values[ROOT + ('scenes', f's_{index:04d}')] = ({'chapter_id': f'ch_{index % chapters:04d}'}
            if index % 2 else {'tab_id': 'tab_main'})
    for index in range(assets):
        values[ROOT + ('assets', f'a_{index:04d}')] = {'name': 'synthetic.png'}
    raw.run_transaction(lambda tx: [tx.create(path, data) for path, data in values.items()])
    trace = TracedStore(raw)
    fence = AccountLifecycleDocumentStore(trace, actor=lambda: EDITOR)
    view = ManuscriptDocumentStore(fence)
    return raw, trace, view, LegacyManuscriptAdoption(view, now=lambda: 1000)


def begin(service):
    return service.begin(PID, EDITOR, generation=7, operation='synthetic_adoption')


def ready(service, progress):
    while not progress['ready']:
        progress = service.step(PID, EDITOR, progress['job_id'], progress['next_step'])
    return progress


def test_adoption_resumes_pages_across_instances_without_activating_partial_proof():
    raw, trace, view, service = world(chapters=101, blocks=205, scenes=105, assets=102)
    progress = begin(service)
    job = progress['job_id']
    assert service.begin(PID, EDITOR, generation=7, operation='synthetic_adoption') == progress
    with pytest.raises(DocumentConflict, match='NOT_READY'):
        service.finish(PID, EDITOR, job)
    assert FORMAT_FIELD not in raw.get(ROOT).data
    while not progress['ready']:
        old_step = progress['next_step']
        service = LegacyManuscriptAdoption(view, now=lambda: 1000)
        trace.calls.clear()
        progress = service.step(PID, EDITOR, job, old_step)
        page_calls = [call for call in trace.calls if call[0] == 'query_collection_page' and call[1][0][-1] != 'members']
        assert len(page_calls) == 1 and len(page_calls[0][3].rows) <= PAGE_SIZE
        assert not any(call[0] in {'query_collection', 'query_collection_in'} for call in trace.calls)
        assert all(len(call[1][0]) <= PAGE_SIZE for call in trace.calls if call[0] == 'get_many')
        assert FORMAT_FIELD not in raw.get(ROOT).data
        trace.calls.clear()
        assert service.step(PID, EDITOR, job, old_step) == progress
        assert not any(call[0] == 'query_collection_page' and call[1][0][-1] != 'members' for call in trace.calls)
    assert progress['next_step'] == 10 and progress['records_validated'] == 515
    assert service.status(PID, EDITOR, job) == progress
    finished = service.finish(PID, EDITOR, job)
    assert finished['activated'] and service.finish(PID, EDITOR, job) == finished
    assert raw.get(ROOT).data == {'status': 'active_manuscript_v1', 'generation': 7, FORMAT_FIELD: 1}
    assert len(view.query_collection(BLOCKS)) == 205


def test_exact_full_page_requires_terminal_probe_and_cannot_skip_steps():
    raw, trace, view, service = world(chapters=100, blocks=100, scenes=0, assets=0)
    progress = begin(service)
    with pytest.raises(DocumentConflict, match='OUT_OF_ORDER'):
        service.step(PID, EDITOR, progress['job_id'], 1)
    progress = ready(service, progress)
    assert progress['next_step'] == 7  # Five collections, two full-page terminal probes.
    assert progress['records_validated'] == 202
    pages = [call[3] for call in trace.calls if call[0] == 'query_collection_page' and call[1][0][-1] != 'members']
    assert sum(not page.rows and page.next_cursor is None for page in pages) == 4


def test_prepare_derives_generation_inside_authorized_transaction_when_omitted():
    raw, trace, view, service = world()
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, 'generation': 8}))
    progress = service.begin(PID, EDITOR, operation='implicit_generation')
    assert progress['source_generation'] == 8 and not progress['ready']
    assert raw.get(service._path(PID, progress['job_id'])).data['source_generation'] == 8
    assert service.begin(PID, EDITOR, operation='implicit_generation') == progress
    with pytest.raises(DocumentConflict, match='GENERATION_CONFLICT'):
        service.begin(PID, EDITOR, operation='implicit_generation', generation=7)


@pytest.mark.parametrize('generation', [None, 7])
def test_already_adopted_prepare_returns_terminal_without_new_job_or_manuscript_scan(generation):
    raw, trace, view, service = world()
    progress = ready(service, begin(service))
    service.finish(PID, EDITOR, progress['job_id'])
    before = deepcopy(raw._documents)
    trace.calls.clear()
    result = service.begin(PID, EDITOR, operation='already_adopted', generation=generation)
    assert result == {'job_id': None, 'next_step': 0, 'records_validated': 0, 'family': None,
        'ready': True, 'activated': True, 'source_generation': 7}
    assert raw._documents == before
    assert not any(call[0] in {'create', 'set', 'delete'} for call in trace.calls)
    assert [call[1][0] for call in trace.calls if call[0].startswith('query_collection')] == [ROOT + ('members',)]
    with pytest.raises(DocumentConflict, match='GENERATION_CONFLICT'):
        service.begin(PID, EDITOR, operation='already_adopted', generation=6)


@pytest.mark.parametrize('change', ['actor', 'membership', 'private', 'actor_lock', 'owner_lock', 'owner_profile', 'format'])
def test_already_adopted_prepare_still_requires_all_live_authority_and_codec_checks(change):
    raw, trace, view, service = world()
    progress = ready(service, begin(service))
    service.finish(PID, EDITOR, progress['job_id'])
    def change_authority(tx):
        if change == 'membership':
            tx.delete(ROOT + ('members', EDITOR))
        elif change == 'private':
            tx.set(ROOT, {**tx.get(ROOT).data, '_bm_private_title': {'sealed': 'synthetic'}})
        elif change.endswith('_lock'):
            tx.create(('account_erasure_locks', EDITOR if change == 'actor_lock' else UID), {'operation': 'synthetic'})
        elif change == 'owner_profile':
            tx.set(('users', UID), {'status': 'erasure_pending'})
        elif change == 'format':
            tx.set(ROOT, {**tx.get(ROOT).data, FORMAT_FIELD: None})
    raw.run_transaction(change_authority)
    before = deepcopy(raw._documents)
    with pytest.raises((HostedValidationError, ProjectUnavailable, DocumentConflict)):
        service.begin(PID, UID if change == 'actor' else EDITOR, operation='terminal_denied')
    assert raw._documents == before


@pytest.mark.parametrize('phase', ['scanning', 'ready'])
def test_generation_change_invalidates_proof_even_for_insert_before_cursor(phase):
    raw, trace, view, service = world(blocks=205)
    progress = begin(service)
    if phase == 'ready':
        progress = ready(service, progress)
    else:
        while progress['family'] != 'blocks':
            progress = service.step(PID, EDITOR, progress['job_id'], progress['next_step'])
        progress = service.step(PID, EDITOR, progress['job_id'], progress['next_step'])
    def concurrent(tx):
        tx.create(BLOCKS + ('a_before_cursor',), {'chapter_id': 'missing'})
        tx.set(ROOT, {**tx.get(ROOT).data, 'generation': 8})
    raw.run_transaction(concurrent)
    for operation in (lambda: service.step(PID, EDITOR, progress['job_id'], progress['next_step']),
            lambda: service.finish(PID, EDITOR, progress['job_id']), lambda: service.status(PID, EDITOR, progress['job_id'])):
        with pytest.raises(DocumentConflict, match='GENERATION_CONFLICT'):
            operation()
    assert FORMAT_FIELD not in raw.get(ROOT).data


@pytest.mark.parametrize('change', ['membership', 'actor', 'owner', 'actor_lock', 'owner_lock', 'root', 'root_operation'])
def test_adoption_rechecks_membership_accounts_and_erasure_before_every_commit(change):
    raw, trace, view, service = world()
    progress = ready(service, begin(service))
    def revoke(tx):
        if change == 'membership':
            tx.delete(ROOT + ('members', EDITOR))
        elif change.endswith('_lock'):
            tx.create(('account_erasure_locks', EDITOR if change == 'actor_lock' else UID), {'operation': 'synthetic'})
        elif change == 'root_operation':
            tx.set(ROOT, {**tx.get(ROOT).data, '_bm_erasure_operation': 'synthetic'})
        else:
            path = ROOT if change == 'root' else ('users', EDITOR if change == 'actor' else UID)
            tx.set(path, {**tx.get(path).data, 'status': 'erasure_pending'})
    raw.run_transaction(revoke)
    for operation in (lambda: service.finish(PID, EDITOR, progress['job_id']),
            lambda: service.step(PID, EDITOR, progress['job_id'], progress['next_step'])):
        with pytest.raises((ProjectUnavailable, DocumentConflict)):
            operation()
    assert FORMAT_FIELD not in raw.get(ROOT).data


@pytest.mark.parametrize('change', ['missing', 'multiple', 'mismatched_identity'])
def test_adoption_preserves_exactly_one_owner_contract_without_unbounded_lookup(change):
    raw, trace, view, service = world()
    def change_owner(tx):
        if change == 'missing':
            tx.delete(ROOT + ('members', UID))
        elif change == 'multiple':
            tx.set(ROOT + ('members', EDITOR), {'user_id': EDITOR, 'role': 'owner'})
        else:
            tx.set(ROOT + ('members', UID), {'user_id': EDITOR, 'role': 'owner'})
    raw.run_transaction(change_owner)
    with pytest.raises(ProjectUnavailable):
        begin(service)
    queries = [call for call in trace.calls if call[0] == 'query_collection_page']
    assert len(queries) == 1 and queries[0][2]['limit'] == 2
    assert not any(call[0] == 'query_collection' for call in trace.calls)


@pytest.mark.parametrize('family', ['tabs', 'chapters', 'blocks', 'scenes', 'assets'])
def test_reserved_markers_on_any_legacy_collection_block_adoption(family):
    raw, trace, view, service = world()
    row = raw.query_collection(ROOT + (family,))[0]
    raw.run_transaction(lambda tx: tx.set(row.path, {**row.data, REVISION_FIELD: 'unexpected'}))
    with pytest.raises(HostedValidationError, match='MARKER_REPAIR_REQUIRED'):
        ready(service, begin(service))
    assert FORMAT_FIELD not in raw.get(ROOT).data


@pytest.mark.parametrize('family,patch', [
    ('chapters', {'tab_id': 'missing'}), ('chapters', {'tab_id': None}),
    ('blocks', {'chapter_id': 'missing'}), ('blocks', {'chapter_id': ['bad']}),
    ('blocks', {'tab_id': 'tab_other'}), ('scenes', {'tab_id': 'missing'}),
    ('scenes', {'chapter_id': 'missing'}), ('scenes', {'chapter_id': 'ch_0000', 'tab_id': 'tab_other'})])
def test_orphan_or_mismatched_parents_require_repair_before_adoption(family, patch):
    raw, trace, view, service = world()
    row = raw.query_collection(ROOT + (family,))[0]
    raw.run_transaction(lambda tx: tx.set(row.path, {**row.data, **patch}))
    with pytest.raises(HostedValidationError, match='PARENT_REPAIR_REQUIRED'):
        ready(service, begin(service))
    assert FORMAT_FIELD not in raw.get(ROOT).data


def test_page_and_publication_failures_are_atomic_and_retries_do_not_double_count(monkeypatch):
    raw, trace, view, service = world()
    progress = begin(service)
    job_path = service._path(PID, progress['job_id'])
    before = raw.get(job_path)
    native = raw.run_transaction
    def crash(fn, **kwargs):
        def interrupted(tx):
            fn(tx)
            raise RuntimeError('SYNTHETIC_COMMIT_INTERRUPTION')
        return native(interrupted, **kwargs)
    monkeypatch.setattr(raw, 'run_transaction', crash)
    with pytest.raises(RuntimeError, match='INTERRUPTION'):
        service.step(PID, EDITOR, progress['job_id'], 0)
    assert raw.get(job_path) == before
    monkeypatch.setattr(raw, 'run_transaction', native)
    raw.retry_next_transaction()
    progress = service.step(PID, EDITOR, progress['job_id'], 0)
    assert progress['next_step'] == 1 and progress['records_validated'] == 2
    progress = ready(service, progress)
    monkeypatch.setattr(raw, 'run_transaction', crash)
    with pytest.raises(RuntimeError, match='INTERRUPTION'):
        service.finish(PID, EDITOR, progress['job_id'])
    assert FORMAT_FIELD not in raw.get(ROOT).data and raw.get(job_path).data['state'] == 'ready'


def test_progress_is_bound_to_actor_project_operation_expiry_and_valid_cursor():
    raw, trace, view, service = world()
    progress = begin(service)
    job = progress['job_id']
    with pytest.raises(HostedValidationError, match='AUTHENTICATED_ACTOR_REQUIRED'):
        service.status(PID, UID, job)
    with pytest.raises(DocumentNotFound):
        service.status(OTHER, EDITOR, job)
    with pytest.raises(DocumentConflict, match='EXPIRED'):
        LegacyManuscriptAdoption(view, now=lambda: 90000).status(PID, EDITOR, job)
    path = service._path(PID, job)
    original = dict(raw.get(path).data)
    for patch in ({'project_id': OTHER}, {'actor_user_id': UID}, {'operation': 'different'},
            {'cursor': 'other/path'}, {'family_index': 6}, {'state': 'ready'}, {'state': []}, {'next_step': True}):
        raw.run_transaction(lambda tx: tx.set(path, {**original, **patch}))
        with pytest.raises((HostedValidationError, DocumentNotFound)):
            service.status(PID, EDITOR, job)
    raw.run_transaction(lambda tx: tx.set(path, original))
    for bad in (True, -1, 0.5):
        with pytest.raises(HostedValidationError, match='STEP_INVALID'):
            service.step(PID, EDITOR, job, bad)


@pytest.mark.parametrize('marker', [None, True, 2])
def test_invalid_physical_root_marker_is_never_treated_as_already_adopted(marker):
    raw, trace, view, service = world()
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, FORMAT_FIELD: marker}))
    with pytest.raises(HostedValidationError, match='FORMAT_UNSUPPORTED'):
        begin(service)


@pytest.mark.parametrize('phase', ['begin', 'step', 'finish'])
def test_private_root_refuses_plaintext_adoption_before_scanning_or_activation(phase):
    raw, trace, view, service = world()
    if phase != 'begin':
        progress = begin(service)
        if phase == 'finish':
            progress = ready(service, progress)
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data,
        '_bm_private_title': {'sealed': 'synthetic-private-root'}}))
    before = deepcopy(raw._documents)
    trace.calls.clear()
    with pytest.raises(HostedValidationError, match='PRIVATE_TITLE_CODEC_REQUIRED'):
        if phase == 'begin':
            begin(service)
        elif phase == 'step':
            service.step(PID, EDITOR, progress['job_id'], progress['next_step'])
        else:
            service.finish(PID, EDITOR, progress['job_id'])
    assert raw._documents == before
    assert not any(call[0].startswith('query_collection') for call in trace.calls)
    assert FORMAT_FIELD not in raw.get(ROOT).data


def test_provider_page_scope_order_missing_cursor_and_explicit_null_predicate():
    raw = MemoryDocumentStore()
    data = {BLOCKS + ('a',): {}, BLOCKS + ('b',): {'style': None}, BLOCKS + ('c',): {'style': 'body'},
        BLOCKS + ('d',): {'style': 'body'}, ('projects', OTHER, 'blocks', 'c'): {'style': 'body'},
        BLOCKS + ('c', 'nested', 'child'): {'style': 'body'}}
    raw.run_transaction(lambda tx: [tx.create(path, value) for path, value in data.items()])
    def inspect(tx):
        one = tx.query_collection_page(BLOCKS, limit=2)
        assert [row.path[-1] for row in one.rows] == ['a', 'b'] and one.next_cursor == 'b'
        two = tx.query_collection_page(BLOCKS, limit=2, after=one.next_cursor)
        assert [row.path[-1] for row in two.rows] == ['c', 'd'] and two.next_cursor == 'd'
        assert tx.query_collection_page(BLOCKS, limit=2, after='d').next_cursor is None
        assert [row.path[-1] for row in tx.query_collection_page(BLOCKS, after='bb').rows] == ['c', 'd']
        assert [row.path[-1] for row in tx.query_collection_page(BLOCKS, field='style', value=None).rows] == ['b']
    raw.run_transaction(inspect)


def test_lifecycle_page_overlay_is_bounded_complete_and_may_underfill():
    raw, trace, view, service = world(blocks=5)
    def overlay(tx):
        tx.delete(BLOCKS + ('b_0000',))
        tx.delete(BLOCKS + ('b_0001',))
        empty = tx.query_collection_page(BLOCKS, limit=2)
        assert not empty.rows and empty.next_cursor == 'b_0001'
        tx.create(BLOCKS + ('a_pending',), {'chapter_id': 'ch_0000'})
        tx.create(BLOCKS + ('b_0002_pending',), {'chapter_id': 'ch_0000'})
        tx.create(BLOCKS + ('z_pending',), {'chapter_id': 'ch_0000'})
        tx.set(BLOCKS + ('b_0003',), {'chapter_id': 'other'})
        rows, cursor = [], None
        while True:
            page = tx.query_collection_page(BLOCKS, limit=2, after=cursor, field='chapter_id', value='ch_0000')
            rows.extend(row.path[-1] for row in page.rows)
            if page.next_cursor is None:
                break
            assert cursor is None or page.next_cursor > cursor
            cursor = page.next_cursor
        assert rows == ['a_pending', 'b_0002', 'b_0002_pending', 'b_0004', 'z_pending']
    view.store.run_transaction(overlay)
    assert all(len(call[3].rows) <= 2 for call in trace.calls if call[0] == 'query_collection_page')
    assert not any(call[0] == 'query_collection' for call in trace.calls)


def google_page(monkeypatch, snapshots):
    calls, transaction = [], object()
    monkeypatch.setitem(sys.modules, 'google.cloud.firestore_v1.base_query',
        SimpleNamespace(FieldFilter=lambda field, op, value: (field, op, value)))
    class Query:
        def where(self, **kwargs): calls.append(('where', kwargs)); return self
        def order_by(self, value): calls.append(('order', value)); return self
        def start_after(self, value): calls.append(('after', value)); return self
        def limit(self, value): calls.append(('limit', value)); return self
        def stream(self, **kwargs): calls.append(('stream', kwargs)); return iter(snapshots)
    def document(path):
        calls.append(('document', path))
        return 'reference:' + path
    client = SimpleNamespace(collection=lambda path: (calls.append(('collection', path)), Query())[1], document=document)
    return _GoogleTransaction(client, transaction), transaction, calls


def snapshot(path, data=None):
    return SimpleNamespace(reference=SimpleNamespace(path='/'.join(path)), to_dict=lambda: data or {})


def test_google_page_uses_scoped_reference_cursor_order_limit_and_same_transaction(monkeypatch):
    tx, transaction, calls = google_page(monkeypatch, [snapshot(BLOCKS + ('b',), {'kind': 'body'})])
    page = tx.query_collection_page(BLOCKS, limit=1, after='a', field='kind', value='body')
    assert page.next_cursor == 'b' and page.rows[0].path == BLOCKS + ('b',)
    assert calls == [('collection', '/'.join(BLOCKS)), ('where', {'filter': ('kind', '==', 'body')}),
        ('order', '__name__'), ('document', '/'.join(BLOCKS + ('a',))),
        ('after', {'__name__': 'reference:' + '/'.join(BLOCKS + ('a',))}),
        ('limit', 1), ('stream', {'transaction': transaction})]


@pytest.mark.parametrize('kwargs', [{'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': 1.1},
    {'after': ''}, {'after': '../x'}, {'after': 'a/b'}, {'after': ['a']}, {'after': '\\'},
    {'field': ''}, {'field': '__name__'}, {'field': 'a.b'}, {'field': '`role`'},
    {'field': 'a/b'}, {'value': 'without_field'}])
def test_page_input_validation_happens_before_provider_io(monkeypatch, kwargs):
    tx, _, calls = google_page(monkeypatch, [])
    raw = MemoryDocumentStore()
    with pytest.raises(HostedValidationError):
        tx.query_collection_page(BLOCKS, **kwargs)
    with pytest.raises(HostedValidationError):
        raw.run_transaction(lambda memory: memory.query_collection_page(BLOCKS, **kwargs))
    assert calls == []


@pytest.mark.parametrize('path', [(), ROOT, ROOT + ('bad/path',), ROOT + ('..',)])
def test_page_collection_scope_is_validated_before_io(monkeypatch, path):
    tx, _, calls = google_page(monkeypatch, [])
    raw = MemoryDocumentStore()
    with pytest.raises(HostedValidationError):
        tx.query_collection_page(path)
    with pytest.raises(HostedValidationError):
        raw.run_transaction(lambda memory: memory.query_collection_page(path))
    assert calls == []


@pytest.mark.parametrize('paths', [[BLOCKS + ('b',), BLOCKS + ('a',)], [BLOCKS + ('a',), BLOCKS + ('a',)],
    [('projects', OTHER, 'blocks', 'b')], [BLOCKS + ('b', 'nested', 'c')],
    [BLOCKS + (str(index),) for index in range(3)]])
def test_google_page_rejects_wrong_scope_order_duplicates_and_provider_overflow(monkeypatch, paths):
    tx, _, calls = google_page(monkeypatch, [snapshot(path) for path in paths])
    with pytest.raises(HostedValidationError, match='PAGE_RESULT_INVALID'):
        tx.query_collection_page(BLOCKS, limit=2)


def test_google_page_rejects_cursor_replay_and_predicate_mismatch(monkeypatch):
    tx, _, _ = google_page(monkeypatch, [snapshot(BLOCKS + ('b',))])
    with pytest.raises(HostedValidationError, match='PAGE_RESULT_INVALID'):
        tx.query_collection_page(BLOCKS, after='b')
    with pytest.raises(HostedValidationError, match='PAGE_RESULT_INVALID'):
        tx.query_collection_page(BLOCKS, field='kind', value=None)
