"""Synthetic reader-contract rehearsal; no activation of the product protocol."""
from copy import deepcopy

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import (DocumentConflict, DocumentNotFound,
    HostedValidationError, ProjectUnavailable)
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedProjectRepository
from hosted.manuscript_visibility import (ASSET_REVISIONS_FIELD, DELETED_FIELD,
    FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD, ManuscriptDocumentStore)

PID = '1' * 32
UID = 'usr_' + '2' * 32
ROOT = ('projects', PID)
TAB = ROOT + ('tabs', 'tab_main')
OLD = ROOT + ('chapters', 'chapter_old')
NEW = ROOT + ('chapters', 'chapter_new')
OLD_BLOCK = ROOT + ('blocks', 'b_old')
NEW_BLOCK = ROOT + ('blocks', 'b_new')


def seed():
    raw = MemoryDocumentStore()
    data = {
        ROOT: {'status': 'active_manuscript_v1', FORMAT_FIELD: 1, 'generation': 3, 'owner_user_id': UID},
        ('users', UID): {'status': 'active'},
        ROOT + ('members', UID): {'role': 'owner', 'user_id': UID},
        TAB: {'tab_id': TAB[-1], 'generation': 1},
        OLD: {'tab_id': TAB[-1], 'generation': 1},
        NEW: {'tab_id': TAB[-1], REVISION_FIELD: 'revision_new', 'generation': 1},
        OLD_BLOCK: {'chapter_id': OLD[-1], 'tab_id': TAB[-1], 'generation': 1},
        NEW_BLOCK: {'chapter_id': NEW[-1], 'tab_id': TAB[-1], 'generation': 1},
        ROOT + ('scenes', 'scene_old'): {'tab_id': TAB[-1], 'generation': 1},
        ROOT + ('scenes', 'scene_new'): {'tab_id': TAB[-1], REVISION_FIELD: 'revision_new', 'generation': 1},
        ROOT + ('annotations', 'a_old'): {'block_id': OLD_BLOCK[-1], 'generation': 1},
        ROOT + ('annotations', 'a_new'): {'block_id': NEW_BLOCK[-1], 'generation': 1},
        ROOT + ('assets', 'asset_legacy'): {'name': 'legacy.png', 'generation': 1},
        ROOT + ('assets', 'asset_new'): {'name': 'new.png', ASSET_REVISIONS_FIELD: [
            {'tab_id': TAB[-1], 'revision': 'revision_new'}], 'generation': 1},
    }
    raw.run_transaction(lambda tx: [tx.create(path, value) for path, value in data.items()])
    fence = AccountLifecycleDocumentStore(raw, actor=lambda: UID)
    return raw, ManuscriptDocumentStore(fence)


def publish(view):
    def change(tx):
        tab = dict(tx.get(TAB).data)
        tab[HEAD_FIELD] = 'revision_new'
        tx.set(TAB, tab)
    view.retained_view().run_transaction(change)


def ids(view, family):
    return [row.path[-1] for row in view.query_collection(ROOT + (family,))]


def test_staged_rows_are_hidden_from_every_normal_read_until_one_pointer_commits():
    raw, view = seed()
    assert view.get(NEW) is None
    assert view.get(NEW_BLOCK) is None
    assert ids(view, 'chapters') == ['chapter_old']
    assert ids(view, 'blocks') == ['b_old']
    assert ids(view, 'scenes') == ['scene_old']
    assert ids(view, 'annotations') == ['a_old']
    assert ids(view, 'assets') == ['asset_legacy']
    assert view.run_transaction(lambda tx: tx.get_many([OLD, NEW]))[1] is None
    assert view.run_transaction(lambda tx: tx.count_collection(ROOT + ('blocks',))) == 1
    assert [row.path[-1] for row in view.query_collection_group('blocks', field='tab_id', value=TAB[-1])] == ['b_old']
    publish(view)
    assert view.get(OLD) is None
    assert view.get(OLD_BLOCK) is None
    assert ids(view, 'chapters') == ['chapter_new']
    assert ids(view, 'blocks') == ['b_new']
    assert ids(view, 'scenes') == ['scene_new']
    assert ids(view, 'annotations') == ['a_new']
    assert ids(view, 'assets') == ['asset_legacy', 'asset_new']
    # Publication does not destroy the previous revision or staging objects.
    assert raw.get(OLD) and raw.get(OLD_BLOCK)


def test_tombstone_hides_large_chapter_without_reading_or_deleting_child_payloads():
    raw, view = seed()
    def remove(tx):
        chapter = dict(tx.get(OLD).data)
        chapter[DELETED_FIELD] = True
        tx.set(OLD, chapter)
    view.retained_view().run_transaction(remove)
    assert view.get(OLD) is None and view.get(OLD_BLOCK) is None
    assert ids(view, 'blocks') == [] and ids(view, 'annotations') == []
    assert raw.get(OLD_BLOCK) is not None
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.set(OLD_BLOCK, {'chapter_id': OLD[-1], 'tab_id': TAB[-1]}))
    before = dict(raw.get(OLD).data)
    before.pop(DELETED_FIELD)
    view.retained_view().run_transaction(lambda tx: tx.set(OLD, before))
    assert view.get(OLD_BLOCK) is not None


def test_normal_writes_cannot_change_pointers_resurrect_hidden_rows_or_forge_markers():
    raw, view = seed()
    with pytest.raises(HostedValidationError, match='RESERVED'):
        view.run_transaction(lambda tx: tx.set(TAB, {**tx.get(TAB).data, HEAD_FIELD: 'revision_new'}))
    with pytest.raises(HostedValidationError, match='RESERVED'):
        view.run_transaction(lambda tx: tx.create(ROOT + ('chapters', 'chapter_forged'),
            {'tab_id': TAB[-1], REVISION_FIELD: 'revision_new'}))
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.delete(NEW_BLOCK))
    publish(view)
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.set(OLD_BLOCK, dict(raw.get(OLD_BLOCK).data)))
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.create(ROOT + ('blocks', 'b_bad'),
            {'chapter_id': OLD[-1], 'tab_id': TAB[-1]}))
    assert raw.get(ROOT + ('blocks', 'b_bad')) is None


def test_new_chapters_inherit_published_revision_and_block_parent_checks_are_atomic():
    raw, view = seed()
    publish(view)
    chapter = ROOT + ('chapters', 'chapter_added')
    block = ROOT + ('blocks', 'b_added')
    def create(tx):
        tx.create(chapter, {'tab_id': TAB[-1]})
        tx.create(block, {'chapter_id': chapter[-1], 'tab_id': TAB[-1]})
    view.run_transaction(create)
    assert raw.get(chapter).data[REVISION_FIELD] == 'revision_new'
    assert view.get(block) is not None
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.set(block, {**tx.get(block).data, 'tab_id': 'tab_wrong'}))
    assert view.get(block).data['tab_id'] == TAB[-1]


def test_active_and_trash_formats_block_unaware_readers_and_restore_preserves_guard():
    raw, view = seed()
    old = HostedProjectRepository(raw)
    with pytest.raises(ProjectUnavailable):
        raw.run_transaction(lambda tx: old._require_member(tx, PID, UID))
    root = view.get(ROOT)
    assert root.data['status'] == 'active' and FORMAT_FIELD not in root.data
    assert len(view.query_collection(('projects',), field='status', value='active')) == 1
    view.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, 'status': 'deleted'}))
    assert raw.get(ROOT).data['status'] == 'deleted_manuscript_v1'
    assert view.get(ROOT).data['status'] == 'deleted'
    view.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, 'status': 'active'}))
    assert raw.get(ROOT).data['status'] == 'active_manuscript_v1'
    assert raw.get(ROOT).data[FORMAT_FIELD] == 1


def test_erasure_marker_is_never_hidden_and_retained_view_keeps_write_fence():
    raw, view = seed()
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data,
        'status': 'erasure_pending', '_bm_erasure_operation': 'closure'}))
    assert view.get(ROOT).data['status'] == 'erasure_pending'
    with pytest.raises(ProjectUnavailable):
        view.retained_view().run_transaction(lambda tx: tx.set(NEW, dict(tx.get(NEW).data)))
    raw.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, 'status': 'active_manuscript_v1',
        '_bm_erasure_operation': None}))
    raw.run_transaction(lambda tx: tx.set(('users', UID), {'status': 'erasure_pending'}))
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        publish(view)


def test_missing_unknown_or_lossy_format_roots_fail_closed():
    raw, view = seed()
    for changes in ({FORMAT_FIELD: 2}, {FORMAT_FIELD: True}, {'status': 'active'}, {'status': 'deleted'}):
        original = deepcopy(dict(raw.get(ROOT).data))
        raw.run_transaction(lambda tx: tx.set(ROOT, {**original, **changes}))
        with pytest.raises(HostedValidationError):
            view.get(ROOT)
        raw.run_transaction(lambda tx: tx.set(ROOT, original))
    original = dict(raw.get(ROOT).data)
    original.pop(FORMAT_FIELD)
    raw.run_transaction(lambda tx: tx.set(ROOT, original))
    with pytest.raises(HostedValidationError, match='MARKER_REQUIRED'):
        view.get(ROOT)


def test_legacy_projects_remain_unchanged_but_marked_staging_never_leaks():
    raw, view = seed()
    def legacy(tx):
        root = dict(tx.get(ROOT).data)
        root.pop(FORMAT_FIELD)
        root['status'] = 'active'
        tx.set(ROOT, root)
    raw.run_transaction(legacy)
    assert view.get(OLD) is not None
    assert view.get(NEW) is None and view.get(NEW_BLOCK) is None
    assert ids(view, 'annotations') == ['a_old']
    assert view.get(ROOT).data == raw.get(ROOT).data
    with pytest.raises(HostedValidationError, match='RESERVED'):
        view.run_transaction(lambda tx: tx.set(ROOT, {**tx.get(ROOT).data, FORMAT_FIELD: 1}))


def test_tabless_legacy_blocks_stay_visible_and_wrong_explicit_tab_is_rejected():
    raw, view = seed()
    data = dict(raw.get(OLD_BLOCK).data)
    data.pop('tab_id')
    raw.run_transaction(lambda tx: tx.set(OLD_BLOCK, data))
    assert view.get(OLD_BLOCK) is not None
    with pytest.raises(DocumentNotFound):
        view.run_transaction(lambda tx: tx.set(OLD_BLOCK, {**data, 'tab_id': 'tab_wrong'}))
    publish(view)
    assert view.get(OLD_BLOCK) is None


def test_block_before_chapter_writes_use_the_completed_overlay():
    raw, view = seed()
    publish(view)
    chapter = ROOT + ('chapters', 'chapter_added')
    block = ROOT + ('blocks', 'b_added')
    def create(tx):
        tx.create(block, {'chapter_id': chapter[-1], 'tab_id': TAB[-1]})
        tx.create(chapter, {'tab_id': TAB[-1]})
    view.run_transaction(create)
    assert view.get(block) is not None
    assert raw.get(chapter).data[REVISION_FIELD] == 'revision_new'


def test_journal_captures_normalized_tags_and_immediate_undo_redo_is_exact():
    from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
    raw, view = seed()
    publish(view)
    actions = ActionDocumentStore(view)
    journal = ActionJournal(actions)
    token = journal.begin(PID, UID)['token']
    chapter = ROOT + ('chapters', 'chapter_added')
    block = ROOT + ('blocks', 'b_added')
    scene = ROOT + ('scenes', 'scene_added')
    def create(tx):
        # Deliberately exercise the order that previously broke normalization.
        tx.create(block, {'chapter_id': chapter[-1], 'tab_id': TAB[-1], 'generation': 1})
        tx.create(chapter, {'tab_id': TAB[-1], 'generation': 1})
        tx.create(scene, {'tab_id': TAB[-1], 'generation': 1})
    with bind_action(token, PID, UID):
        actions.run_transaction(create)
    assert journal.finish(PID, UID, token)['changed']
    entry = raw.get(('hosted_actions', UID, 'tokens', token)).data
    after = {tuple(record['path']): record['after'] for record in entry['records']}
    assert after[chapter] == raw.get(chapter).data
    assert after[scene] == raw.get(scene).data
    assert journal.restore(PID, UID, token, 'undo')['changed']
    assert view.get(chapter) is None and view.get(block) is None
    assert journal.restore(PID, UID, token, 'redo')['changed']
    assert view.get(block) is not None
    assert raw.get(chapter).data['generation'] > after[chapter]['generation']


def test_journal_cannot_use_normalization_to_forge_a_publication_marker():
    from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
    raw, view = seed()
    actions = ActionDocumentStore(view)
    token = ActionJournal(actions).begin(PID, UID)['token']
    before = deepcopy(raw.get(TAB))
    with bind_action(token, PID, UID), pytest.raises(HostedValidationError, match='RESERVED'):
        actions.run_transaction(lambda tx: tx.set(TAB, {**tx.get(TAB).data, HEAD_FIELD: 'revision_new'}))
    assert raw.get(TAB) == before


@pytest.mark.parametrize("chapter_first", [False, True])
def test_cross_tab_move_is_normalized_before_journaling_and_undo_redo(chapter_first):
    from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
    raw, view = seed()
    other = ROOT + ('tabs', 'tab_other')
    raw.run_transaction(lambda tx: tx.create(other, {'tab_id': other[-1], HEAD_FIELD: 'revision_other', 'generation': 1}))
    actions = ActionDocumentStore(view)
    journal = ActionJournal(actions)
    token = journal.begin(PID, UID)['token']
    def move(tx):
        chapter = dict(tx.get(OLD).data)
        block = dict(tx.get(OLD_BLOCK).data)
        chapter['tab_id'] = block['tab_id'] = other[-1]
        chapter['generation'] += 1
        block['generation'] += 1
        writes = [(OLD, chapter), (OLD_BLOCK, block)] if chapter_first else [(OLD_BLOCK, block), (OLD, chapter)]
        for path, data in writes:
            tx.set(path, data)
    with bind_action(token, PID, UID):
        actions.run_transaction(move)
    journal.finish(PID, UID, token)
    assert view.get(OLD).data[REVISION_FIELD] == 'revision_other'
    assert view.get(OLD_BLOCK).data['tab_id'] == other[-1]
    journal.restore(PID, UID, token, 'undo')
    assert REVISION_FIELD not in view.get(OLD).data
    assert view.get(OLD_BLOCK).data['tab_id'] == TAB[-1]
    journal.restore(PID, UID, token, 'redo')
    assert view.get(OLD).data[REVISION_FIELD] == 'revision_other'


def test_prepared_permission_is_exact_scoped_and_does_not_trust_mutated_result():
    raw, view = seed()
    publish(view)
    path = ROOT + ('chapters', 'chapter_prepared')
    def bad(tx):
        result = tx.prepare_mutations({path: {'tab_id': TAB[-1], 'title': 'Exact'}})
        result[path]['title'] = 'Changed after validation'
        tx.create(path, result[path])
    with pytest.raises(HostedValidationError, match='RESERVED'):
        view.run_transaction(bad)
    assert raw.get(path) is None
    # A separate transaction does not inherit any normalization authority.
    with pytest.raises(HostedValidationError, match='RESERVED'):
        view.run_transaction(lambda tx: tx.create(path, {'tab_id': TAB[-1], 'title': 'Exact',
            REVISION_FIELD: 'revision_new'}))


def test_corrupt_revision_and_tombstone_types_fail_closed():
    raw, view = seed()
    for path, field, value in [(TAB, HEAD_FIELD, True), (OLD, REVISION_FIELD, 1),
            (OLD, DELETED_FIELD, 0)]:
        before = deepcopy(dict(raw.get(path).data))
        raw.run_transaction(lambda tx: tx.set(path, {**before, field: value}))
        with pytest.raises(HostedValidationError):
            view.get(OLD)
        raw.run_transaction(lambda tx: tx.set(path, before))
    asset = ROOT + ('assets', 'asset_new')
    raw.run_transaction(lambda tx: tx.set(asset, {**tx.get(asset).data, ASSET_REVISIONS_FIELD: [
        {'tab_id': TAB[-1], 'revision': True}]}))
    with pytest.raises(HostedValidationError):
        view.get(asset)
