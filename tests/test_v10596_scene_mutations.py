"""Atomic Scene View prose persistence and unsupported-structure boundaries."""
from copy import deepcopy

import pytest

from hosted.domain_store import HostedDomainStore
from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.scene_mutations import save_scene_blocks
from tests.test_v10354 import _services, OWNER, READER


@pytest.fixture
def scene():
    docs, objects, _, _, _, projects = _services()
    pid = projects.create('Synthetic scene saves')['id']
    domain = projects.domain
    chapter = domain.get('chapter', {'project_id': pid, 'chapter_id': 'chapter_1'}, user_id=OWNER)
    sid = domain.create('scene', {'project_id': pid}, {'tab_id': chapter['tab_id'], 'title': 'Scene'}, user_id=OWNER)['id']
    domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_1'},
        {'scene_id': sid, 'block_type': 'paragraph', 'text_content': 'First'}, user_id=OWNER)
    second = domain.create('chapter', {'project_id': pid}, {'tab_id': chapter['tab_id'], 'position': 1}, user_id=OWNER)
    block = domain.create('manuscript_block', {'project_id': pid}, {'chapter_id': second['id'],
        'tab_id': chapter['tab_id'], 'scene_id': sid, 'position': 0, 'block_type': 'paragraph', 'text_content': 'Second'}, user_id=OWNER)
    outside = domain.create('manuscript_block', {'project_id': pid}, {'chapter_id': 'chapter_1',
        'tab_id': chapter['tab_id'], 'position': 1, 'text_content': 'Outside'}, user_id=OWNER)
    blocks = [{'id': 'b_1', 'chapter_id': 'chapter_1', 'text_content': 'Edited first'},
        {'id': block['id'], 'chapter_id': second['id'], 'text_content': 'Edited second'}]
    revision = domain.project_generation(pid, OWNER)
    return docs, objects, domain, pid, sid, blocks, {row['chapter_id']: str(revision) for row in blocks}, outside


def save(scene, *, domain=None, operation='scene_save', **changes):
    _, _, original, pid, sid, blocks, revisions, _ = scene
    return save_scene_blocks(domain or original, project_id=pid, scene_id=sid, user_id=changes.pop('user_id', OWNER),
        blocks=changes.pop('blocks', blocks), expected_revisions=changes.pop('expected_revisions', revisions), operation=operation)


def canonical(scene):
    docs, _, _, pid, *_ = scene
    return {row.path: deepcopy(row.data) for collection in ('blocks', 'scenes', 'chapters', 'mutation_receipts')
        for row in docs.query_collection(('projects', pid, collection))}, deepcopy(docs.get(('projects', pid)).data)


def test_multi_chapter_save_single_revision_and_durable_replay(scene):
    before = canonical(scene)
    result = save(scene)
    expected = str(int(next(iter(scene[6].values()))) + 1)
    assert set(result['chapter_revisions'].values()) == {expected}
    replica = HostedDomainStore(scene[0], scene[1])
    assert save(scene, domain=replica) == result
    assert replica.project_generation(scene[3], OWNER) == int(expected)
    for incoming in scene[5]:
        row = replica.get('manuscript_block', {'project_id': scene[3], 'block_id': incoming['id']}, user_id=OWNER)
        assert row['text_content'] == incoming['text_content']
        assert row['content_html'] == incoming['text_content']
    outside = ('projects', scene[3], 'blocks', scene[7]['id'])
    assert scene[0].get(outside).data == before[0][outside]
    committed = canonical(scene)
    with pytest.raises(DocumentConflict):
        save(scene, operation='stale_writer')
    assert canonical(scene) == committed
    with pytest.raises(DocumentConflict, match='IDEMPOTENCY_KEY_REUSED'):
        save(scene, blocks=[{**row, 'text_content': 'Different'} for row in scene[5]])


@pytest.mark.parametrize('kind', ['delete', 'insert', 'move', 'missing_revision', 'managed', 'foreign_scene'])
def test_unsupported_or_malformed_requests_leave_all_canonical_state_unchanged(scene, kind):
    before = canonical(scene)
    blocks = deepcopy(scene[5]); options = {}
    if kind == 'delete': blocks.pop()
    if kind == 'insert': blocks.append({'id': 'b_new', 'chapter_id': blocks[0]['chapter_id'], 'text_content': 'new'})
    if kind == 'move': blocks[0]['chapter_id'] = blocks[1]['chapter_id']
    if kind == 'missing_revision': options['expected_revisions'] = {}
    if kind == 'managed': blocks[0]['payload_object'] = {}
    if kind == 'foreign_scene': blocks[0]['scene_id'] = 'scene_foreign'
    with pytest.raises((HostedValidationError, DocumentConflict)):
        save(scene, blocks=blocks, **options)
    assert canonical(scene) == before


def test_stage_failure_cannot_commit_first_of_two_edits(scene, monkeypatch):
    before = canonical(scene); native = scene[2]._publish_hybrid; calls = []
    def publish(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise RuntimeError('Synthetic object failure')
        return native(*args, **kwargs)
    monkeypatch.setattr(scene[2], '_publish_hybrid', publish)
    with pytest.raises(RuntimeError, match='Synthetic object failure'): save(scene)
    assert canonical(scene) == before


def test_revocation_during_stage_prevents_publication(scene, monkeypatch):
    before = canonical(scene); native = scene[2]._publish_hybrid
    def publish(*args, **kwargs):
        pointer = native(*args, **kwargs)
        scene[0].run_transaction(lambda tx: tx.delete(('projects', scene[3], 'members', OWNER)))
        return pointer
    monkeypatch.setattr(scene[2], '_publish_hybrid', publish)
    with pytest.raises(ProjectUnavailable): save(scene)
    assert canonical(scene) == before


def test_reader_denied_before_object_staging(scene, monkeypatch):
    monkeypatch.setattr(scene[2], '_publish_hybrid', lambda *a, **kw: pytest.fail('Unauthorized staging'))
    with pytest.raises(ProjectUnavailable): save(scene, user_id=READER)


def test_transaction_retry_and_interleaving_writer(scene, monkeypatch):
    native = scene[0].run_transaction
    def retry(fn, **kwargs):
        if fn.__name__ == 'mutate': scene[0].retry_next_transaction()
        return native(fn, **kwargs)
    monkeypatch.setattr(scene[0], 'run_transaction', retry)
    result = save(scene)
    assert set(result['chapter_revisions'].values()) == {str(int(next(iter(scene[6].values()))) + 1)}


def test_concurrent_edit_during_staging_is_not_overwritten(scene, monkeypatch):
    native = scene[2]._publish_hybrid; calls = []
    replica = HostedDomainStore(scene[0], scene[1])
    def publish(*args, **kwargs):
        pointer = native(*args, **kwargs)
        if not calls:
            calls.append(1)
            replica.update('manuscript_block', {'project_id': scene[3], 'block_id': 'b_1'},
                {'text_content': 'Concurrent wins'}, user_id=OWNER)
        return pointer
    monkeypatch.setattr(scene[2], '_publish_hybrid', publish)
    with pytest.raises(DocumentConflict): save(scene)
    row = replica.get('manuscript_block', {'project_id': scene[3], 'block_id': 'b_1'}, user_id=OWNER)
    assert row['text_content'] == 'Concurrent wins'
    second = replica.get('manuscript_block', {'project_id': scene[3], 'block_id': scene[5][1]['id']}, user_id=OWNER)
    assert second['text_content'] == 'Second'


def test_scene_save_is_one_undoable_action(scene):
    from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
    domain = HostedDomainStore(ActionDocumentStore(scene[0]), scene[1])
    journal = ActionJournal(domain.documents)
    token = journal.begin(scene[3], OWNER)['token']
    with bind_action(token, scene[3], OWNER):
        save(scene, domain=domain)
    journal.finish(scene[3], OWNER, token)
    journal.restore(scene[3], OWNER, token, 'undo')
    assert domain.get('manuscript_block', {'project_id': scene[3], 'block_id': 'b_1'}, user_id=OWNER)['text_content'] == 'First'
    assert domain.get('manuscript_block', {'project_id': scene[3], 'block_id': scene[5][1]['id']}, user_id=OWNER)['text_content'] == 'Second'
    journal.restore(scene[3], OWNER, token, 'redo')
    assert domain.get('manuscript_block', {'project_id': scene[3], 'block_id': 'b_1'}, user_id=OWNER)['text_content'] == 'Edited first'


def test_http_scene_read_revision_and_atomic_save_contract(scene, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hosted.idempotency import bind_operations, request_scope_key
    import hosted.http_api as http
    import hosted.http_compat as compat
    monkeypatch.setattr(http, '_domain', lambda: scene[2])
    monkeypatch.setattr(http, '_pid', lambda request: scene[3])
    monkeypatch.setattr(http, '_uid', lambda: OWNER)
    app = FastAPI(); app.include_router(http.product_router); app.include_router(compat.router)
    path = f'/api/scenes/{scene[4]}/blocks'
    scope = request_scope_key('synthetic-scene-http', actor=OWNER, method='PUT', path=path)
    with TestClient(app) as client:
        response = client.get(f'/api/scenes/{scene[4]}/manuscript')
        assert response.status_code == 200, response.text
        assert response.json()['chapter_revisions'] == scene[6]
        assert [row['id'] for row in response.json()['blocks']] == [row['id'] for row in scene[5]]
        payload = {'blocks': scene[5], 'expected_revisions': response.json()['chapter_revisions']}
        with bind_operations(scope): first = client.put(path, json=payload)
        with bind_operations(scope): replay = client.put(path, json=payload)
        assert first.status_code == replay.status_code == 200, (first.text, replay.text)
        assert first.json() == replay.json()
        stale = client.put(path, json=payload)
        assert stale.status_code == 409
        assert client.post(f'/api/scenes/{scene[4]}/remove-blocks', json={'ids': ['b_1']}).status_code == 409


def test_scene_read_race_does_not_return_mixed_revision(scene, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    import hosted.http_api as http
    monkeypatch.setattr(http, '_domain', lambda: scene[2])
    monkeypatch.setattr(http, '_pid', lambda request: scene[3])
    monkeypatch.setattr(http, '_uid', lambda: OWNER)
    native = scene[2].list
    # Save uses list too, so perform the mutation through an independent replica.
    replica = HostedDomainStore(scene[0], scene[1])
    def raced(entity, *args, **kwargs):
        rows = native(entity, *args, **kwargs)
        if entity == 'manuscript_block': save(scene, domain=replica)
        return rows
    monkeypatch.setattr(scene[2], 'list', raced)
    with pytest.raises(HTTPException) as failure:
        http.hosted_scene_manuscript(scene[4], Request({'type': 'http'}))
    assert failure.value.status_code == 409


def test_large_scene_stages_only_changed_blocks(scene, monkeypatch):
    domain, pid, sid = scene[2:5]
    first = domain.get('manuscript_block', {'project_id': pid, 'block_id': 'b_1'}, user_id=OWNER)
    for index in range(1, 160):
        domain.create('manuscript_block', {'project_id': pid}, {'chapter_id': 'chapter_1',
            'tab_id': first['tab_id'], 'scene_id': sid, 'position': index, 'text_content': f'Unchanged {index}'}, user_id=OWNER)
    current = domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER, field='scene_id', value=sid)
    current.sort(key=lambda row: (0 if row['chapter_id'] == 'chapter_1' else 1, row['position'], row['id']))
    blocks = [{'id': row['id'], 'chapter_id': row['chapter_id'], 'text_content': row['text_content']} for row in current]
    blocks[0]['text_content'] = 'Only one changed paragraph'
    revision = domain.project_generation(pid, OWNER)
    publishes = []; native = domain._publish_hybrid
    def publish(*args, **kwargs):
        publishes.append(args[1]['block_id'])
        return native(*args, **kwargs)
    monkeypatch.setattr(domain, '_publish_hybrid', publish)
    result = save(scene, blocks=blocks, expected_revisions={row['chapter_id']: str(revision) for row in current})
    assert publishes == ['b_1']
    assert len(result['ids']) == 161


def test_private_scene_save_remains_closed_without_plaintext_receipts(scene):
    scene[2].encrypted_objects = object()
    before = canonical(scene)
    with pytest.raises(HostedValidationError, match='PRIVATE_SAVE_UNAVAILABLE'): save(scene)
    assert canonical(scene) == before
