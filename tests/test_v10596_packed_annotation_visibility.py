"""Bounded synthetic packed-reader rehearsal, not revision writer activation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.domain_store import HostedDomainStore
from hosted.editing_packs import FIELD, PackedAnnotations, annotation_view
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.firestore.memory import MemoryDocumentStore
from hosted.manuscript_visibility import DELETED_FIELD, FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD, ManuscriptDocumentStore
from hosted.runtime_services import ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore

PID, UID, EDITOR = '1' * 32, 'usr_' + '2' * 32, 'usr_' + '3' * 32
ROOT = ('projects', PID)


@pytest.fixture
def packed():
    raw, objects = MemoryDocumentStore(), MemoryObjectStore()
    ordinary = HostedDomainStore(raw, objects)
    ensure_hosted_user(raw, user_id=UID, display_name='Synthetic owner')
    ensure_hosted_user(raw, user_id=EDITOR, display_name='Synthetic editor')
    ordinary.projects.create('Synthetic packed revisions', owner_user_id=UID, operation_id='create', project_id=PID)
    raw.run_transaction(lambda tx: tx.create(ROOT + ('members', EDITOR), {'role': 'editor', 'user_id': EDITOR}))
    tab = ordinary.create('document_tab', {'project_id': PID}, {'name': 'Main', 'is_manuscript': True}, user_id=UID)
    label = ordinary.create('markup_label', {'project_id': PID}, {'name': 'Adverbs', 'color': '#123abc'}, user_id=UID)
    chapters, blocks = [], []
    for title, text in [('Old', 'quietly'), ('New', 'softly')]:
        chapter = ordinary.create('chapter', {'project_id': PID}, {'title': title, 'tab_id': tab['id']}, user_id=UID)
        block = ordinary.create('manuscript_block', {'project_id': PID}, {'chapter_id': chapter['id'],
            'tab_id': tab['id'], 'text_content': text, 'content_html': text}, user_id=UID)
        chapters.append(chapter)
        blocks.append(block)
    native = ordinary.create('annotation', {'project_id': PID}, {'block_id': blocks[0]['id'],
        'label_id': label['id'], 'annotation_type': 'highlight', 'selected_text': 'quietly',
        'start_offset': 0, 'end_offset': 7}, user_id=UID)
    suffix, stamp = 'a' * 24, ordinary.clock.now_iso()
    hid = 'epack_' + suffix
    head = dict(id=hid, annotation_id=hid, project_id=PID, annotation_type='editing-pack/v1',
        tab_id=tab['id'], label_id=label['id'], scan='adverbs', generation=1, created_at=stamp, updated_at=stamp)
    rows = []
    for i, block in enumerate([blocks[0], blocks[0], blocks[1]]):
        iid = f'eann_{suffix}_{i:024x}'
        rows.append(dict(id=iid, annotation_id=iid, project_id=PID, block_id=block['id'], chapter_id=None,
            annotation_type='highlight', label_id=label['id'], start_offset=0, end_offset=len(block['text_content']),
            selected_text=block['text_content'], prefix_context='', suffix_context='', body='editing-search/v1:adverbs',
            status='open', stale=False, source_generation=block['generation'], generation=1,
            created_at=stamp, updated_at=stamp))
    staged = PackedAnnotations(ordinary).stage(PID, head, rows)
    def seed(tx):
        tx.create(ROOT + ('annotations', hid), staged)
        path = ROOT + ('chapters', chapters[1]['id'])
        tx.set(path, {**tx.get(path).data, REVISION_FIELD: 'revision_new'})
        tx.set(ROOT, {**tx.get(ROOT).data, FORMAT_FIELD: 1, 'status': 'active_manuscript_v1'})
    raw.run_transaction(seed)
    lifecycle = AccountLifecycleDocumentStore(raw, actor=lambda: UID)
    view = ManuscriptDocumentStore(lifecycle)
    domain = HostedDomainStore(ActionDocumentStore(view), objects)
    return SimpleNamespace(raw=raw, objects=objects, ordinary=ordinary, view=view, domain=domain,
        packs=PackedAnnotations(domain), head=staged, rows=rows, chapters=chapters, blocks=blocks,
        native=native, tab=tab, label=label)


def switch(packed, *, new=True):
    """Test-only pointer publication, always with the canonical generation bump."""
    def write(tx):
        path = ROOT + ('tabs', packed.tab['id'])
        tab = dict(tx.get(path).data)
        if new:
            tab[HEAD_FIELD] = 'revision_new'
        else:
            tab.pop(HEAD_FIELD, None)
        tx.set(path, tab)
        root = dict(tx.get(ROOT).data)
        tx.set(ROOT, {**root, 'generation': root['generation'] + 1})
    packed.raw.run_transaction(write)


def read(packed, reader, *, uid=UID, iid=None):
    if reader == 'list':
        return packed.domain.list('annotation', {'project_id': PID}, user_id=uid)
    if reader == 'view':
        return annotation_view(packed.domain, PID, uid)
    return packed.domain.get('annotation', {'project_id': PID, 'annotation_id': iid or packed.rows[0]['id']}, user_id=uid)


@pytest.mark.parametrize('reader', ['list', 'view'])
def test_hidden_sources_are_omitted_and_restored_without_rewriting_packs(packed, reader):
    before = deepcopy(packed.raw.get(ROOT + ('annotations', packed.head['id'])))
    objects = packed.objects.list_prefix('projects/')
    old = {packed.rows[0]['id'], packed.rows[1]['id'], packed.native['id']}
    assert {row['id'] for row in read(packed, reader)} == old
    switch(packed)
    visible = read(packed, reader)
    assert [row['id'] for row in visible] == [packed.rows[2]['id']]
    assert not visible[0]['stale']
    switch(packed, new=False)
    assert {row['id'] for row in read(packed, reader)} == old
    assert packed.raw.get(ROOT + ('annotations', packed.head['id'])) == before
    assert packed.objects.list_prefix('projects/') == objects
    assert len(packed.packs.rows(PID, packed.head)) == packed.head[FIELD]['count'] == 3


def test_get_and_scoped_lists_hide_staged_retired_and_tombstoned_sources(packed):
    with pytest.raises(DocumentNotFound):
        read(packed, 'get', iid=packed.rows[2]['id'])
    assert read(packed, 'get')['id'] == packed.rows[0]['id']
    switch(packed)
    with pytest.raises(DocumentNotFound):
        read(packed, 'get')
    assert packed.packs.list(PID, UID, field='block_id', value=packed.blocks[0]['id']) == []
    assert [row['id'] for row in annotation_view(packed.domain, PID, UID, tab=packed.tab['id'])] == [packed.rows[2]['id']]
    assert annotation_view(packed.domain, PID, UID, chapter=packed.chapters[0]['id']) == []
    path = ROOT + ('chapters', packed.chapters[1]['id'])
    packed.raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, DELETED_FIELD: True}))
    assert read(packed, 'list') == read(packed, 'view') == []
    with pytest.raises(DocumentNotFound):
        read(packed, 'get', iid=packed.rows[2]['id'])


@pytest.mark.parametrize('patch', [None, {'status': 'resolved'}, {'stale': False}])
def test_hidden_individual_mutations_are_rejected_even_in_bound_actions(packed, patch):
    switch(packed)
    token = ActionJournal(packed.domain.documents).begin(PID, UID)['token']
    before = packed.raw.get(ROOT + ('annotations', packed.head['id']))
    objects = packed.objects.list_prefix('projects/')
    with bind_action(token, PID, UID), pytest.raises(DocumentNotFound):
        if patch is None:
            packed.domain.delete('annotation', {'project_id': PID, 'annotation_id': packed.rows[0]['id']}, user_id=UID)
        else:
            packed.domain.update('annotation', {'project_id': PID, 'annotation_id': packed.rows[0]['id']}, patch, user_id=UID)
    assert packed.raw.get(ROOT + ('annotations', packed.head['id'])) == before
    assert packed.objects.list_prefix('projects/') == objects


@pytest.mark.parametrize('delete', [False, True])
def test_visible_mutation_preserves_hidden_siblings_in_the_immutable_body(packed, delete):
    switch(packed)
    token = ActionJournal(packed.domain.documents).begin(PID, UID)['token']
    old_bytes = b''.join(packed.objects.read_chunks(packed.head[FIELD]['object_name'], max_bytes=100_000))
    ids = {'project_id': PID, 'annotation_id': packed.rows[2]['id']}
    with bind_action(token, PID, UID):
        if delete:
            packed.domain.delete('annotation', ids, user_id=UID)
        else:
            changed = packed.domain.update('annotation', ids, {'stale': False, 'status': 'resolved'}, user_id=UID)
            assert changed['status'] == 'resolved' and not changed['stale']
    assert ActionJournal(packed.domain.documents).finish(PID, UID, token)['changed']
    current = packed.raw.get(ROOT + ('annotations', packed.head['id'])).data
    body = packed.packs.rows(PID, current)
    assert current[FIELD]['count'] == len(body) == (2 if delete else 3)
    assert {row['id'] for row in body} >= {row['id'] for row in packed.rows[:2]}
    assert b''.join(packed.objects.read_chunks(packed.head[FIELD]['object_name'], max_bytes=100_000)) == old_bytes
    switch(packed, new=False)
    assert {row['id'] for row in read(packed, 'list')} == {packed.rows[0]['id'], packed.rows[1]['id'], packed.native['id']}


@pytest.mark.parametrize('wrapped', [False, True])
def test_legacy_missing_sources_stay_stale_and_editable(packed, wrapped):
    def legacy(tx):
        root = dict(tx.get(ROOT).data)
        root.pop(FORMAT_FIELD)
        tx.set(ROOT, {**root, 'status': 'active'})
        tx.delete(ROOT + ('blocks', packed.blocks[0]['id']))
    packed.raw.run_transaction(legacy)
    if not wrapped:
        packed.domain = packed.ordinary
        packed.packs = PackedAnnotations(packed.domain)
    row = read(packed, 'get')
    assert row['stale']
    assert row['id'] in {item['id'] for item in read(packed, 'list')}
    assert row['id'] in {item['id'] for item in read(packed, 'view')}
    changed = packed.packs.mutate(PID, UID, row['id'], {'status': 'resolved'})
    assert changed['stale'] and changed['status'] == 'resolved'
    # A tagged staging source stays hidden even before the root is upgraded.
    if wrapped:
        with pytest.raises(DocumentNotFound):
            read(packed, 'get', iid=packed.rows[2]['id'])


def test_revision_missing_source_preserves_orphan_staleness(packed):
    packed.domain.update('manuscript_block', {'project_id': PID, 'block_id': packed.blocks[0]['id']},
        {'text_content': 'A new sentence.'}, user_id=UID)
    assert read(packed, 'get')['stale']
    packed.raw.run_transaction(lambda tx: tx.delete(ROOT + ('blocks', packed.blocks[0]['id'])))
    orphan = read(packed, 'get')
    assert orphan['stale']
    assert orphan['id'] in {row['id'] for row in read(packed, 'list')}
    assert orphan['id'] in {row['id'] for row in read(packed, 'view')}


@pytest.mark.parametrize('reader', ['list', 'get', 'view'])
@pytest.mark.parametrize('change', ['generation', 'membership', 'actor_erasure', 'owner_erasure', 'root_erasure', 'authority'])
def test_changes_during_pack_io_fail_closed(packed, monkeypatch, reader, change):
    original = packed.objects.read_chunks
    denied = [False]
    packed.domain.editing_authority = lambda tx, uid, pid, action: not denied[0]
    def changed(*args, **kwargs):
        data = original(*args, **kwargs)
        def write(tx):
            if change == 'generation':
                tx.set(ROOT, {**tx.get(ROOT).data, 'generation': tx.get(ROOT).data['generation'] + 1})
            elif change == 'membership':
                tx.delete(ROOT + ('members', EDITOR))
            elif change in {'actor_erasure', 'owner_erasure'}:
                uid = EDITOR if change == 'actor_erasure' else UID
                tx.set(('account_erasure_locks', uid), {'operation': 'synthetic'})
            elif change == 'root_erasure':
                tx.set(ROOT, {**tx.get(ROOT).data, 'status': 'erasure_pending'})
            else:
                denied[0] = True
        packed.raw.run_transaction(write)
        return data
    monkeypatch.setattr(packed.objects, 'read_chunks', changed)
    error = DocumentConflict if change == 'generation' else (DocumentNotFound, ProjectUnavailable)
    with pytest.raises(error):
        read(packed, reader, uid=EDITOR)


@pytest.mark.parametrize('reader', ['list', 'get', 'view'])
def test_source_lookup_rechecks_authority_and_generation_in_its_transaction(packed, monkeypatch, reader):
    # No unfenced document.get is allowed to fetch a source after pack I/O.
    original = packed.domain.documents.get
    def no_source_get(path):
        assert tuple(path)[2:3] != ('blocks',)
        return original(path)
    monkeypatch.setattr(packed.domain.documents, 'get', no_source_get)
    from hosted.manuscript_visibility import _RevisionTransaction
    lookup = _RevisionTransaction.get_many
    calls = []
    def batched(tx, paths):
        if paths and all(path[2:3] == ('blocks',) for path in paths):
            assert tx.original[ROOT].data['generation'] == packed.raw.get(ROOT).data['generation']
            assert ROOT + ('members', UID) in tx.original
            assert ('account_erasure_locks', UID) in tx.original
            calls.append(paths)
        return lookup(tx, paths)
    monkeypatch.setattr(_RevisionTransaction, 'get_many', batched)
    read(packed, reader)
    assert len(calls) == 1
    assert len(calls[0]) == (1 if reader == 'get' else 2)
    assert len(calls[0]) == len(set(calls[0]))


@pytest.mark.parametrize('reader', ['list', 'get', 'view'])
def test_hidden_payload_still_requires_full_pack_integrity(packed, monkeypatch, reader):
    switch(packed)
    original = packed.objects.read_chunks
    def corrupt(name, **kwargs):
        data = b''.join(original(name, **kwargs))
        return [data[:-1] + bytes([data[-1] ^ 1])]
    monkeypatch.setattr(packed.objects, 'read_chunks', corrupt)
    with pytest.raises(HostedValidationError, match='PACK_INTEGRITY'):
        read(packed, reader)


@pytest.mark.parametrize('change', ['generation', 'membership', 'erasure'])
@pytest.mark.parametrize('reader', ['list', 'get', 'view'])
def test_final_fence_rejects_changes_after_source_projection(packed, monkeypatch, change, reader):
    original = PackedAnnotations._project_rows
    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        def write(tx):
            if change == 'generation':
                tx.set(ROOT, {**tx.get(ROOT).data, 'generation': tx.get(ROOT).data['generation'] + 1})
            elif change == 'membership':
                tx.delete(ROOT + ('members', UID))
            else:
                tx.set(('account_erasure_locks', UID), {'operation': 'synthetic'})
        packed.raw.run_transaction(write)
        return result
    monkeypatch.setattr(PackedAnnotations, '_project_rows', changed)
    error = DocumentConflict if change == 'generation' else (DocumentNotFound, ProjectUnavailable)
    with pytest.raises(error):
        read(packed, reader)


@pytest.mark.parametrize('change', ['revision', 'membership', 'erasure'])
def test_mutation_cannot_publish_after_source_or_authority_changes_during_staging(packed, monkeypatch, change):
    before = packed.raw.get(ROOT + ('annotations', packed.head['id']))
    original = packed.packs.stage
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'revision':
            switch(packed)
        elif change == 'membership':
            packed.raw.run_transaction(lambda tx: tx.delete(ROOT + ('members', UID)))
        else:
            packed.raw.run_transaction(lambda tx: tx.set(('account_erasure_locks', UID), {'operation': 'synthetic'}))
        return result
    monkeypatch.setattr(packed.packs, 'stage', changed)
    error = DocumentConflict if change == 'revision' else (DocumentNotFound, ProjectUnavailable)
    with pytest.raises(error):
        packed.packs.mutate(PID, UID, packed.rows[0]['id'], {'status': 'resolved'})
    assert packed.raw.get(ROOT + ('annotations', packed.head['id'])) == before
    assert packed.raw.get(ROOT + ('blocks', packed.blocks[0]['id'])) is not None


def test_hidden_rows_still_require_valid_schema_and_full_count(packed):
    bad = deepcopy(packed.rows)
    bad[2]['start_offset'] = -1  # Hidden staged source must not hide corruption.
    staged = packed.packs.stage(PID, packed.head, bad)
    packed.raw.run_transaction(lambda tx: tx.set(ROOT + ('annotations', packed.head['id']), staged))
    with pytest.raises(HostedValidationError, match='PACK_INVALID'):
        read(packed, 'list')


def test_erasure_inventory_keeps_hidden_pack_sources_and_immutable_objects(packed):
    before = {row.path for row in packed.raw.project_document_inventory(PID)}
    objects = packed.objects.list_prefix(f'projects/{PID}/')
    switch(packed)
    assert len(read(packed, 'list')) == 1
    assert {row.path for row in packed.raw.project_document_inventory(PID)} == before
    assert ROOT + ('blocks', packed.blocks[0]['id']) in before
    assert ROOT + ('annotations', packed.head['id']) in before
    assert packed.objects.list_prefix(f'projects/{PID}/') == objects
