"""Synthetic indexed reader costs, not runtime activation or live latency proof."""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.firestore.google_driver import _GoogleTransaction
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedProjectRepository
from hosted.manuscript_visibility import (DELETED_FIELD, FORMAT_FIELD, HEAD_FIELD,
    REVISION_FIELD, ManuscriptDocumentStore)

PID, UID = '1' * 32, 'usr_' + '2' * 32
ROOT = ('projects', PID)
CHAPTERS, BLOCKS = ROOT + ('chapters',), ROOT + ('blocks',)
TAB = ROOT + ('tabs', 'tab_main')


class TracedStore:
    """Record logical provider reads and reject reads after provider writes."""
    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def run_transaction(self, fn, **kwargs):
        calls = self.calls
        class Transaction:
            def __init__(self, tx):
                self.tx, self.writing = tx, False

            def __getattr__(self, name):
                operation = getattr(self.tx, name)
                def call(*args, **kwargs):
                    if name in {'create', 'set', 'delete'}:
                        self.writing = True
                    else:
                        assert not self.writing, 'Firestore forbids reads after writes'
                    result = operation(*args, **kwargs)
                    calls.append((name, args, kwargs, result))
                    return result
                return call
        return self.raw.run_transaction(lambda tx: fn(Transaction(tx)), **kwargs)


def world(*, chapters=2, retired_novels=0, tombstone_blocks=0, legacy=False):
    raw = MemoryDocumentStore()
    data = {
        ROOT: {'status': 'active_manuscript_v1', FORMAT_FIELD: 1, 'generation': 1, 'owner_user_id': UID},
        ('users', UID): {'status': 'active'},
        ROOT + ('members', UID): {'user_id': UID, 'role': 'owner'},
        TAB: {HEAD_FIELD: 'revision_live'},
        CHAPTERS + ('chapter_deleted',): {'tab_id': TAB[-1], REVISION_FIELD: 'revision_live', DELETED_FIELD: True},
    }
    for index in range(chapters):
        cid = f'chapter_{index:03d}'
        data[CHAPTERS + (cid,)] = {'tab_id': TAB[-1], REVISION_FIELD: 'revision_live'}
        for paragraph in range(2):
            data[BLOCKS + (f'b_{index:03d}_{paragraph}',)] = {
                'chapter_id': cid, 'tab_id': TAB[-1], 'semantic_style': 'heading2' if paragraph else 'body'}
    for index in range(tombstone_blocks):
        data[BLOCKS + (f'deleted_{index:05d}',)] = {'chapter_id': 'chapter_deleted', 'tab_id': TAB[-1]}
    for novel in range(retired_novels):
        for chapter in range(100):
            cid = f'old_{novel}_{chapter:03d}'
            data[CHAPTERS + (cid,)] = {'tab_id': TAB[-1], REVISION_FIELD: f'revision_old_{novel}'}
            for paragraph in range(100):
                data[BLOCKS + (f'{cid}_{paragraph:03d}',)] = {'chapter_id': cid, 'tab_id': TAB[-1]}
    if legacy:
        data[ROOT].pop(FORMAT_FIELD)
        data[ROOT]['status'] = 'active'
        data[TAB].pop(HEAD_FIELD)
        for path, value in data.items():
            if path[:-1] == CHAPTERS and not value.get(DELETED_FIELD):
                value.pop(REVISION_FIELD, None)
    raw.run_transaction(lambda tx: [tx.create(path, value) for path, value in data.items()])
    trace = TracedStore(raw)
    fenced = AccountLifecycleDocumentStore(trace, actor=lambda: UID)
    return raw, trace, ManuscriptDocumentStore(fenced)


@pytest.mark.parametrize('retired_novels,tombstone_blocks', [(0, 0), (3, 10000)])
@pytest.mark.parametrize('count', [False, True])
def test_live_block_reads_do_not_fetch_retained_novels_or_tombstoned_children(retired_novels, tombstone_blocks, count):
    raw, trace, view = world(chapters=61, retired_novels=retired_novels, tombstone_blocks=tombstone_blocks)
    before = len(raw._documents)
    result = view.run_transaction(lambda tx: tx.count_collection(BLOCKS) if count else tx.query_collection(BLOCKS))
    assert (result if count else len(result)) == 122
    queries = [call for call in trace.calls if call[0].startswith('query_collection')]
    assert [(call[0], call[1][0][-1], len(call[3])) for call in queries] == [
        ('query_collection', 'tabs', 1), ('query_collection', 'chapters', 62),
        ('query_collection_in', 'blocks', 60), ('query_collection_in', 'blocks', 60),
        ('query_collection_in', 'blocks', 2)]
    assert queries[1][2] == {'field': REVISION_FIELD, 'value': 'revision_live'}
    batches = [call[2]['values'] for call in queries[2:]]
    assert [len(values) for values in batches] == [30, 30, 1]
    assert len({cid for values in batches for cid in values}) == 61
    assert all('chapter_deleted' not in values for values in batches)
    assert [call[1][0] for call in trace.calls if call[0] == 'get'] == [ROOT]
    assert len(trace.calls) == 6  # Root + tabs + chapter metadata + three IN queries.
    assert len(raw._documents) == before  # No mutation or implicit reclamation.


def test_chapter_lists_counts_and_scoped_blocks_share_point_visibility():
    raw, trace, view = world(chapters=2, retired_novels=1, tombstone_blocks=100)
    raw.run_transaction(lambda tx: tx.set(BLOCKS + ('b_000_0',), {'chapter_id': 'chapter_000'}))
    raw.run_transaction(lambda tx: tx.set(BLOCKS + ('b_001_0',), {'chapter_id': 'chapter_001', 'tab_id': 'wrong'}))
    for family in (CHAPTERS, BLOCKS):
        paths = [row.path for row in raw.query_collection(family)]
        expected = view.run_transaction(lambda tx: [row.path for row in tx.get_many(paths) if row is not None])
        trace.calls.clear()
        assert [row.path for row in view.query_collection(family)] == expected
        assert view.run_transaction(lambda tx: tx.count_collection(family)) == len(expected)
    assert [row.path[-1] for row in view.query_collection(BLOCKS, field='semantic_style', value='heading2')] == ['b_000_1', 'b_001_1']
    trace.calls.clear()
    assert len(view.query_collection(BLOCKS, field='chapter_id', value='chapter_000')) == 2
    assert [call[0] for call in trace.calls] == ['get', 'get', 'get', 'query_collection_in']
    for hidden in ('chapter_deleted', 'old_0_000', 'missing'):
        trace.calls.clear()
        assert view.query_collection(BLOCKS, field='chapter_id', value=hidden) == []
        assert not any(call[0].startswith('query_collection') for call in trace.calls)


def test_legacy_runtime_query_shape_and_orphan_semantics_stay_unchanged():
    raw, trace, view = world(legacy=True)
    orphan = BLOCKS + ('orphan',)
    raw.run_transaction(lambda tx: tx.create(orphan, {'chapter_id': 'missing', 'tab_id': 'missing'}))
    assert view.get(orphan) is not None
    trace.calls.clear()
    assert orphan in [row.path for row in view.query_collection(BLOCKS)]
    queries = [call for call in trace.calls if call[0].startswith('query_collection')]
    assert [(call[0], call[1][0], call[2]) for call in queries] == [
        ('query_collection', BLOCKS, {'field': None, 'value': None})]
    assert raw.get(ROOT).data['status'] == 'active' and FORMAT_FIELD not in raw.get(ROOT).data


def test_legacy_head_uses_tab_index_and_enabled_orphans_remain_hidden():
    raw, trace, view = world(legacy=True)
    orphan = BLOCKS + ('orphan',)
    staged = CHAPTERS + ('staged',)
    def upgrade(tx):
        tx.set(ROOT, {**tx.get(ROOT).data, 'status': 'active_manuscript_v1', FORMAT_FIELD: 1})
        tx.create(orphan, {'chapter_id': 'missing'})
        tx.create(staged, {'tab_id': TAB[-1], REVISION_FIELD: 'unpublished'})
    raw.run_transaction(upgrade)
    assert len(view.query_collection(BLOCKS)) == 4
    assert view.get(orphan) is None and view.get(staged) is None
    chapter_queries = [call for call in trace.calls if call[0] == 'query_collection' and call[1][0] == CHAPTERS]
    assert [call[2] for call in chapter_queries] == [{'field': 'tab_id', 'value': TAB[-1]}]


@pytest.mark.parametrize('source_family,anchor_field', [('blocks', 'block_id'), ('chapters', 'chapter_id')])
def test_enabled_orphan_annotations_remain_stale_visible_but_present_hidden_sources_do_not(source_family, anchor_field):
    raw, trace, view = world()
    annotations = ROOT + ('annotations',)
    retired_chapter = CHAPTERS + ('retired',)
    data = {
        retired_chapter: {'tab_id': TAB[-1], REVISION_FIELD: 'revision_old'},
        BLOCKS + ('retired',): {'chapter_id': 'retired'},
        BLOCKS + ('chapter_deleted',): {'chapter_id': 'chapter_deleted'},
        annotations + ('orphan',): {anchor_field: 'absent_source'},
        annotations + ('retired',): {anchor_field: 'retired'},
        annotations + ('deleted',): {anchor_field: 'chapter_deleted'},
    }
    raw.run_transaction(lambda tx: [tx.create(path, value) for path, value in data.items()])
    assert view.get(annotations + ('orphan',)) is not None
    assert view.get(annotations + ('retired',)) is None
    assert view.get(annotations + ('deleted',)) is None
    assert [row.path[-1] for row in view.query_collection(annotations)] == ['orphan']
    def packed_sources(tx):
        return [tx.annotation_source_visible(PID, {anchor_field: source})
            for source in ('absent_source', 'retired', 'chapter_deleted')]
    assert view.run_transaction(packed_sources) == [True, False, False]
    assert view.retained_view().run_transaction(packed_sources) == [True, True, True]
    # Orphan annotations remain editable on upgrade; no silent migration/drop.
    view.run_transaction(lambda tx: tx.set(annotations + ('orphan',), {anchor_field: 'absent_source', 'status': 'resolved'}))
    assert raw.get(annotations + ('orphan',)).data['status'] == 'resolved'


def test_retained_queries_and_physical_inventory_are_unfiltered():
    raw, trace, view = world(retired_novels=1, tombstone_blocks=100)
    expected = len(raw.query_collection(BLOCKS))
    assert len(view.retained_view().query_collection(BLOCKS)) == expected == 10104
    assert [call[0] for call in trace.calls] == ['query_collection']
    assert len(view.project_document_inventory(PID)) == len(raw.project_document_inventory(PID))


def test_multiple_tab_heads_select_each_revision_once_and_recheck_tab_ownership():
    raw, trace, view = world()
    def seed(tx):
        tx.create(ROOT + ('tabs', 'tab_shared'), {HEAD_FIELD: 'revision_live'})
        tx.create(ROOT + ('tabs', 'tab_other'), {HEAD_FIELD: 'revision_other'})
        tx.create(ROOT + ('tabs', 'tab_legacy'), {})
        for cid, tid, revision in [('shared', 'tab_shared', 'revision_live'),
                ('other', 'tab_other', 'revision_other'), ('legacy', 'tab_legacy', None),
                ('wrong', 'tab_other', 'revision_live'), ('orphan', 'missing', 'revision_live')]:
            data = {'tab_id': tid}
            if revision is not None:
                data[REVISION_FIELD] = revision
            tx.create(CHAPTERS + (cid,), data)
            tx.create(BLOCKS + (cid,), {'chapter_id': cid, 'tab_id': tid})
    raw.run_transaction(seed)
    assert [row.path[-1] for row in view.query_collection(BLOCKS)] == [
        'b_000_0', 'b_000_1', 'b_001_0', 'b_001_1', 'legacy', 'other', 'shared']
    queries = [call[2] for call in trace.calls if call[0] == 'query_collection' and call[1][0] == CHAPTERS]
    assert len(queries) == 3
    assert {tuple(query.items()) for query in queries} == {
        (('field', REVISION_FIELD), ('value', 'revision_live')),
        (('field', REVISION_FIELD), ('value', 'revision_other')),
        (('field', 'tab_id'), ('value', 'tab_legacy'))}


def test_deleted_tab_and_chapter_tombstones_never_trigger_a_fallback_block_scan():
    raw, trace, view = world(tombstone_blocks=100)
    raw.run_transaction(lambda tx: tx.delete(TAB))
    assert view.query_collection(BLOCKS) == []
    assert [(call[0], call[1][0]) for call in trace.calls] == [
        ('get', ROOT), ('query_collection', ROOT + ('tabs',))]


def test_membership_provider_failure_propagates_without_unfiltered_fallback(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    raw, trace, view = world()
    before = deepcopy(raw._documents)
    def unavailable(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_INDEX_UNAVAILABLE')
    monkeypatch.setattr(_MemoryTransaction, 'query_collection_in', unavailable)
    with pytest.raises(RuntimeError, match='INDEX_UNAVAILABLE'):
        view.query_collection(BLOCKS)
    assert not any(call[0] == 'query_collection' and call[1][0] == BLOCKS for call in trace.calls)
    assert raw._documents == before


@pytest.mark.parametrize('changes', [{FORMAT_FIELD: 2}, {FORMAT_FIELD: True}, {'status': 'active'},
    {FORMAT_FIELD: None}, {FORMAT_FIELD: None, 'status': 'active'}])
def test_invalid_format_fails_before_indexed_collection_reads(changes):
    raw, trace, view = world()
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, **changes}))
    with pytest.raises(HostedValidationError):
        view.query_collection(BLOCKS)
    assert [(call[0], call[1][0]) for call in trace.calls] == [('get', ROOT)]
    with pytest.raises(HostedValidationError):
        view.get(ROOT)


def test_pending_block_writes_and_chapter_deletions_stay_visible_before_provider_writes():
    raw, trace, view = world()
    target, removed = BLOCKS + ('b_added',), CHAPTERS + ('chapter_001',)
    def mutate(tx):
        tx.create(target, {'chapter_id': 'chapter_000', 'tab_id': TAB[-1]})
        tx.delete(BLOCKS + ('b_000_0',))
        tx.delete(removed)
        assert [row.path[-1] for row in tx.query_collection(BLOCKS)] == ['b_000_1', 'b_added']
        assert tx.count_collection(BLOCKS) == 2
    view.run_transaction(mutate)
    assert raw.get(target) and raw.get(removed) is None
    assert any(call[0] == 'query_collection_in' for call in trace.calls)
    assert trace.calls[-1][0] in {'create', 'set', 'delete'}


def test_prepared_pending_chapter_and_block_are_included_without_scanning_retired_rows():
    raw, trace, view = world()
    chapter, block = CHAPTERS + ('chapter_added',), BLOCKS + ('b_added',)
    def mutate(tx):
        prepared = tx.prepare_mutations({block: {'chapter_id': chapter[-1], 'tab_id': TAB[-1]}, chapter: {'tab_id': TAB[-1]}})
        for path, value in prepared.items():
            tx.create(path, value)
        assert block in [row.path for row in tx.query_collection(BLOCKS)]
        assert chapter in [row.path for row in tx.query_collection(CHAPTERS)]
    view.run_transaction(mutate)
    assert raw.get(chapter).data[REVISION_FIELD] == 'revision_live'


def test_lifecycle_membership_overlay_preserves_pending_predicates_and_deep_copies():
    raw, trace, view = world()
    fence = view.store
    def mutate(tx):
        tx.set(BLOCKS + ('b_000_0',), {'chapter_id': 'chapter_001'})
        tx.set(BLOCKS + ('b_001_0',), {'chapter_id': 'chapter_000'})
        tx.delete(BLOCKS + ('b_000_1',))
        tx.create(BLOCKS + ('added',), {'chapter_id': 'chapter_000', 'nested': {'value': 'original'}})
        result = tx.query_collection_in(BLOCKS, field='chapter_id', values=['chapter_000'])
        assert [row.path[-1] for row in result] == ['added', 'b_001_0']
        result[0].data['nested']['value'] = 'changed copy'
        assert tx.get(BLOCKS + ('added',)).data['nested']['value'] == 'original'
    fence.run_transaction(mutate)
    assert raw.get(BLOCKS + ('added',)).data['nested']['value'] == 'original'


@pytest.mark.parametrize('change', ['member', 'account', 'root'])
def test_indexed_queries_keep_existing_membership_and_write_erasure_fences(change):
    raw, trace, view = world()
    projects = HostedProjectRepository(view)
    def read(tx):
        projects._require_member(tx, PID, UID)
        return tx.query_collection(BLOCKS)
    assert len(view.run_transaction(read)) == 4
    def revoke(tx):
        if change == 'member':
            tx.delete(ROOT + ('members', UID))
        else:
            path = ('users', UID) if change == 'account' else ROOT
            tx.set(path, {**tx.get(path).data, 'status': 'erasure_pending'})
    raw.run_transaction(revoke)
    trace.calls.clear()
    if change != 'account':
        with pytest.raises(ProjectUnavailable):
            view.run_transaction(read)
        assert not any(call[0].startswith('query_collection') for call in trace.calls)
    if change != 'member':
        before = deepcopy(raw.get(BLOCKS + ('b_000_0',)))
        def mutate(tx):
            tx.query_collection(BLOCKS)
            tx.set(before.path, {**before.data, 'position': 2})
        with pytest.raises((DocumentConflict, ProjectUnavailable)):
            view.run_transaction(mutate)
        assert raw.get(before.path) == before


@pytest.mark.parametrize('invalid', [[], ['id'] * 31, 'id', [None], [1], [True], [''], [['id']]])
def test_membership_provider_bounds_are_validated_before_io(invalid):
    raw = MemoryDocumentStore()
    def no_io(*args, **kwargs):
        pytest.fail('Invalid membership reached the provider')
    google = _GoogleTransaction(SimpleNamespace(collection=no_io), object())
    for call in (lambda: google.query_collection_in(BLOCKS, field='chapter_id', values=invalid),
            lambda: raw.run_transaction(lambda tx: tx.query_collection_in(BLOCKS, field='chapter_id', values=invalid))):
        with pytest.raises(HostedValidationError, match='MEMBERSHIP_QUERY_INVALID'):
            call()


def test_google_membership_uses_real_in_filter_same_transaction_and_valid_paths(monkeypatch):
    # A provider-shaped fake keeps the ordinary suite cloud/SDK independent.
    monkeypatch.setitem(sys.modules, 'google.cloud.firestore_v1.base_query',
        SimpleNamespace(FieldFilter=lambda field, op, value: (field, op, value)))
    transaction, calls = object(), []
    snapshot = SimpleNamespace(reference=SimpleNamespace(path='/'.join(BLOCKS + ('b_one',))),
        to_dict=lambda: {'chapter_id': 'one'})
    class Collection:
        def where(self, **kwargs):
            calls.append(kwargs)
            return self
        def stream(self, **kwargs):
            calls.append(kwargs)
            return [snapshot]
    tx = _GoogleTransaction(SimpleNamespace(collection=lambda path: (calls.append(path), Collection())[1]), transaction)
    assert tx.query_collection_in(BLOCKS, field='chapter_id', values=['one', 'one', 'two'])[0].path[-1] == 'b_one'
    assert calls == ['/'.join(BLOCKS), {'filter': ('chapter_id', 'in', ['one', 'two'])}, {'transaction': transaction}]
    calls.clear()
    for path in (ROOT, ROOT + ('bad/path',)):
        with pytest.raises(HostedValidationError):
            tx.query_collection_in(path, field='chapter_id', values=['one'])
    for field in (None, '', 1):
        with pytest.raises(HostedValidationError, match='MEMBERSHIP_QUERY_INVALID'):
            tx.query_collection_in(BLOCKS, field=field, values=['one'])
    assert not calls
