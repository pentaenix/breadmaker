"""Synthetic search reconciliation across retained manuscript revisions."""
from copy import deepcopy
import gzip
import json

import pytest

from hosted.action_journal import ActionJournal, bind_action
from hosted.editing_packs import FIELD
from hosted.editing_search import HostedEditingSearch
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.manuscript_visibility import DELETED_FIELD
from tests.test_v10596_packed_annotation_visibility import packed, switch, PID, UID, ROOT


def status(packed):
    return HostedEditingSearch(packed.domain).status(PID, UID, packed.tab['id'])['searches']['adverbs']


def run(packed, action, *, service=None):
    service = service or HostedEditingSearch(packed.domain)
    journal = ActionJournal(packed.domain.documents)
    token = journal.begin(PID, UID)['token']
    with bind_action(token, PID, UID):
        result = service.run(PID, UID, {'scan': 'adverbs', 'action': action, 'tab_id': packed.tab['id']})
    journal.finish(PID, UID, token)
    return token, result


def body(packed, head=None):
    head = head or packed.raw.get(ROOT + ('annotations', packed.head['id'])).data
    data = b''.join(packed.objects.read_chunks(head[FIELD]['object_name'], max_bytes=100_000))
    return json.loads(gzip.decompress(data))['rows']


def anchors(rows):
    # Logical generation/updated_at belong to the head, not an anchor's source.
    return {row['id']: {key: value for key, value in row.items() if key not in {'generation', 'updated_at'}} for row in rows}


def test_status_counts_live_payloads_without_changing_physical_counts_or_reading_prose(packed, monkeypatch):
    before = packed.raw.get(ROOT + ('annotations', packed.head['id']))
    objects = packed.objects.list_prefix('projects/')
    original = packed.objects.read_chunks
    reads = []
    def only_pack(name, **kwargs):
        assert '/annotation-packs/' in name
        reads.append(name)
        return original(name, **kwargs)
    monkeypatch.setattr(packed.objects, 'read_chunks', only_pack)
    assert status(packed)['matches'] == 2
    switch(packed)
    assert status(packed)['matches'] == 1
    path = ROOT + ('chapters', packed.chapters[1]['id'])
    packed.raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, DELETED_FIELD: True}))
    assert status(packed)['matches'] == 0
    switch(packed, new=False)
    assert status(packed)['matches'] == 2
    assert len(reads) == 4
    assert packed.raw.get(ROOT + ('annotations', packed.head['id'])) == before
    assert before.data[FIELD]['count'] == 3
    assert packed.objects.list_prefix('projects/') == objects


@pytest.mark.parametrize('action,added,removed', [('scan', 0, 0), ('recheck', 1, 1), ('clear', 0, 1)])
def test_active_search_preserves_hidden_siblings_and_undo_redo(packed, action, added, removed):
    switch(packed)
    original = body(packed)
    hidden = anchors(original[:2])
    original_bytes = b''.join(packed.objects.read_chunks(packed.head[FIELD]['object_name'], max_bytes=100_000))
    token, result = run(packed, action)
    assert (result['added'], result['removed'], result['matches']) == (added, removed, 0 if action == 'clear' else 1)
    after = body(packed)
    assert {key: value for key, value in anchors(after).items() if key in hidden} == hidden
    assert len(after) == (2 if action == 'clear' else 3)
    assert b''.join(packed.objects.read_chunks(packed.head[FIELD]['object_name'], max_bytes=100_000)) == original_bytes
    assert status(packed)['matches'] == (0 if action == 'clear' else 1)
    journal = ActionJournal(packed.domain.documents)
    journal.restore(PID, UID, token, 'undo')
    assert status(packed)['matches'] == 1
    assert anchors(body(packed)) == anchors(original)
    journal.restore(PID, UID, token, 'redo')
    assert anchors(body(packed)) == anchors(after)
    switch(packed, new=False)
    assert status(packed)['matches'] == 2
    assert {row['id'] for row in packed.packs.list(PID, UID)} >= set(hidden)


@pytest.mark.parametrize('action', ['scan', 'recheck', 'clear'])
def test_missing_source_stays_stale_and_is_reconciled_while_retired_sources_survive(packed, action):
    switch(packed)
    packed.raw.run_transaction(lambda tx: tx.delete(ROOT + ('blocks', packed.blocks[1]['id'])))
    assert packed.packs.get(PID, UID, packed.rows[2]['id'])[2]['stale']
    assert status(packed)['matches'] == 1
    _, result = run(packed, action)
    assert result['matches'] == result['added'] == 0 and result['removed'] == 1
    assert set(anchors(body(packed))) == {row['id'] for row in packed.rows[:2]}
    assert status(packed)['matches'] == 0


def test_repeat_rechecks_refresh_visible_source_generation_and_keep_hidden_anchors(packed):
    switch(packed)
    hidden = anchors(body(packed)[:2])
    bid = packed.blocks[1]['id']
    packed.domain.update('manuscript_block', {'project_id': PID, 'block_id': bid}, {'text_content': 'softly'}, user_id=UID)
    assert packed.packs.get(PID, UID, packed.rows[2]['id'])[2]['stale']
    for action in ['scan', 'recheck', 'recheck']:
        _, result = run(packed, action)
        assert result['added'] == result['removed'] == result['matches'] == 1
        live = packed.packs.list(PID, UID)
        assert len(live) == 1 and not live[0]['stale']
        assert live[0]['source_generation'] == packed.raw.get(ROOT + ('blocks', bid)).data['generation']
        assert {key: value for key, value in anchors(body(packed)).items() if key in hidden} == hidden
        assert len(body(packed)) == 3


def test_clear_native_owned_annotations_uses_chapter_membership_without_duplicate_tab(packed):
    path = ROOT + ('blocks', packed.blocks[0]['id'])
    def omit(tx):
        row = dict(tx.get(path).data)
        row.pop('tab_id')
        tx.set(path, row)
    packed.raw.run_transaction(omit)
    native = packed.domain.create('annotation', {'project_id': PID}, {
        'block_id': packed.blocks[0]['id'], 'label_id': packed.label['id'], 'annotation_type': 'highlight',
        'body': 'editing-search/v1:adverbs', 'start_offset': 0, 'end_offset': 7, 'selected_text': 'quietly'}, user_id=UID)
    assert status(packed)['matches'] == 3
    _, result = run(packed, 'clear')
    assert result['removed'] == 3
    assert packed.raw.get(ROOT + ('annotations', native['id'])) is None
    assert packed.raw.get(ROOT + ('annotations', packed.native['id'])) is not None
    assert [row['id'] for row in body(packed)] == [packed.rows[2]['id']]


@pytest.mark.parametrize('action', ['scan', 'recheck', 'clear'])
def test_all_hidden_sources_remain_in_pack_after_search(packed, action):
    switch(packed)
    path = ROOT + ('chapters', packed.chapters[1]['id'])
    packed.raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, DELETED_FIELD: True}))
    before = anchors(body(packed))
    _, result = run(packed, action)
    assert result['matches'] == result['added'] == result['removed'] == 0
    assert anchors(body(packed)) == before
    assert status(packed)['matches'] == 0


@pytest.mark.parametrize('action', ['scan', 'recheck', 'clear'])
def test_other_document_packs_and_manual_highlights_are_not_rewritten(packed, action):
    tab = packed.domain.create('document_tab', {'project_id': PID}, {'name': 'Other'}, user_id=UID)
    chapter = packed.domain.create('chapter', {'project_id': PID}, {'title': 'Other', 'tab_id': tab['id']}, user_id=UID)
    packed.domain.create('manuscript_block', {'project_id': PID}, {'chapter_id': chapter['id'], 'tab_id': tab['id'],
        'text_content': 'loudly'}, user_id=UID)
    service = HostedEditingSearch(packed.domain)
    token = ActionJournal(packed.domain.documents).begin(PID, UID)['token']
    with bind_action(token, PID, UID):
        service.run(PID, UID, {'scan': 'adverbs', 'tab_id': tab['id']})
    ActionJournal(packed.domain.documents).finish(PID, UID, token)
    other = next(row for row in packed.raw.query_collection(ROOT + ('annotations',)) if row.data.get('tab_id') == tab['id'])
    manual = packed.raw.get(ROOT + ('annotations', packed.native['id']))
    switch(packed)
    run(packed, action)
    assert packed.raw.get(other.path) == other
    assert packed.raw.get(manual.path) == manual


@pytest.mark.parametrize('change', ['generation', 'membership', 'erasure', 'authority'])
def test_status_rechecks_permission_and_generation_after_pack_io(packed, monkeypatch, change):
    original = packed.objects.read_chunks
    allowed = [True]
    packed.domain.editing_authority = lambda *args: allowed[0]
    def read(name, **kwargs):
        result = original(name, **kwargs)
        if change == 'generation':
            switch(packed)
        elif change == 'membership':
            packed.raw.run_transaction(lambda tx: tx.delete(ROOT + ('members', UID)))
        elif change == 'erasure':
            packed.raw.run_transaction(lambda tx: tx.set(('account_erasure_locks', UID), {'operation': 'synthetic'}))
        else:
            allowed[0] = False
        return result
    monkeypatch.setattr(packed.objects, 'read_chunks', read)
    error = DocumentConflict if change == 'generation' else (DocumentNotFound, ProjectUnavailable)
    with pytest.raises(error):
        status(packed)


@pytest.mark.parametrize('change', ['generation', 'membership', 'erasure'])
def test_recheck_never_publishes_after_staging_race(packed, monkeypatch, change):
    switch(packed)
    service = HostedEditingSearch(packed.domain)
    original = service.packs.stage
    before = packed.raw.get(ROOT + ('annotations', packed.head['id']))
    label = packed.raw.get(ROOT + ('markup_labels', packed.label['id']))
    def stage(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'generation':
            switch(packed, new=False)
        elif change == 'membership':
            packed.raw.run_transaction(lambda tx: tx.delete(ROOT + ('members', UID)))
        else:
            packed.raw.run_transaction(lambda tx: tx.set(('account_erasure_locks', UID), {'operation': 'synthetic'}))
        return result
    monkeypatch.setattr(service.packs, 'stage', stage)
    error = DocumentConflict if change == 'generation' else (DocumentNotFound, ProjectUnavailable)
    with pytest.raises(error):
        run(packed, 'recheck', service=service)
    assert packed.raw.get(before.path) == before
    assert packed.raw.get(label.path) == label


@pytest.mark.parametrize('method', ['status', 'scan', 'recheck', 'clear'])
def test_hidden_entries_do_not_bypass_full_pack_validation(packed, method):
    malformed = deepcopy(packed.rows)
    malformed[2]['start_offset'] = -1
    staged = packed.packs.stage(PID, packed.head, malformed)
    packed.raw.run_transaction(lambda tx: tx.set(ROOT + ('annotations', packed.head['id']), staged))
    with pytest.raises(HostedValidationError, match='PACK_INVALID'):
        status(packed) if method == 'status' else run(packed, method)


@pytest.mark.parametrize('limit', ['rows', 'pack_rows', 'bytes'])
def test_retained_entries_count_toward_unchanged_physical_limits(packed, monkeypatch, limit):
    import hosted.editing_packs as packs_module
    import hosted.editing_search as search_module
    switch(packed)
    packed.domain.update('manuscript_block', {'project_id': PID, 'block_id': packed.blocks[1]['id']},
        {'text_content': 'softly loudly'}, user_id=UID)
    before = packed.raw.get(ROOT + ('annotations', packed.head['id']))
    if limit == 'rows':
        monkeypatch.setattr(search_module, 'MAX_ROWS', 3)
    elif limit == 'pack_rows':
        monkeypatch.setattr(packs_module, 'MAX_PACK_ROWS', 3)
    else:
        monkeypatch.setattr(search_module, 'MAX_TOTAL_DECODED', before.data[FIELD]['decoded_bytes'] + 100)
    with pytest.raises(HostedValidationError, match='EDITING_SEARCH_TOO_'):
        run(packed, 'recheck')
    assert packed.raw.get(before.path) == before


def test_selection_clear_denies_retired_source_and_keeps_untargeted_siblings(packed):
    switch(packed)
    service = HostedEditingSearch(packed.domain)
    before = anchors(body(packed))
    with pytest.raises(DocumentNotFound):
        service.clear_selected(PID, UID, {'items': [{'block_id': packed.blocks[0]['id'], 'start_offset': 0, 'end_offset': 7}]})
    assert anchors(body(packed)) == before
    result = service.clear_selected(PID, UID, {'items': [{'block_id': packed.blocks[1]['id'], 'start_offset': 0, 'end_offset': 6}]})
    assert result['changed'] == 1
    assert anchors(body(packed)) == {key: value for key, value in before.items() if key != packed.rows[2]['id']}
