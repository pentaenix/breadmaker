"""Partial hosted prose edits keep their derived HTML and durable semantics."""
import json

import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.chapter_mutations import replace_chapter_blocks
from hosted.domain_store import HostedDomainStore
from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.native_package import prepare_project_package
from hosted.packages import HostedProjectPackageService
from security.content_security import html_from_text, is_safe_inline_html
from tests.test_v10354 import _services, OWNER, READER


@pytest.fixture
def prose():
    docs, objects, _, _, _, projects = _services()
    pid = projects.create('Synthetic partial prose')['id']
    domain = projects.domain
    ids = {'project_id': pid, 'block_id': 'b_1'}
    domain.update('manuscript_block', ids, {'block_type': 'paragraph', 'text_content': 'Old rich prose',
                  'content_html': '<strong>Old</strong> rich prose'}, user_id=OWNER)
    return docs, objects, projects, domain, ids


def edit(prose, boundary, patch, *, operation='partial_edit', expected_revision=None):
    _, _, _, domain, ids = prose
    if boundary == 'domain':
        return domain.update('manuscript_block', ids, patch, user_id=OWNER, operation=operation,
                             expected_project_revision=expected_revision)
    result = replace_chapter_blocks(domain, project_id=ids['project_id'], chapter_id='chapter_1', user_id=OWNER,
        blocks=[{'id': 'b_1', **patch}], expected_revision=expected_revision, operation=operation)
    return domain.get('manuscript_block', ids, user_id=OWNER)


@pytest.mark.parametrize('boundary', ['domain', 'chapter_http_owner'])
def test_changed_plain_text_derives_safe_html_and_remains_portable(prose, boundary):
    text = 'New <script>literal</script> & "quoted" prose.\nSecond 😀 line.'
    saved = edit(prose, boundary, {'text_content': text})
    assert saved['content_html'] == html_from_text(text)
    assert saved['text_content'] == text and is_safe_inline_html(saved['content_html'], text)
    package = HostedProjectPackageService(prose[2]).export_bytes(prose[4]['project_id'], OWNER)
    plan = prepare_project_package(package, 'd'*32)
    block = next(row['data'] for row in plan['records'] if row['entity'] == 'manuscript_block')
    assert block['text_content'] == text and block['content_html'] == saved['content_html']


@pytest.mark.parametrize('boundary', ['domain', 'chapter_http_owner'])
def test_explicit_rich_html_unchanged_text_and_metadata_patches_keep_markup(prose, boundary):
    fragment = '<em>New</em> <b>rich</b> prose'
    edit(prose, boundary, {'text_content': 'New rich prose', 'content_html': fragment})
    unchanged = edit(prose, boundary, {'text_content': 'New rich prose'}, operation='same_text')
    assert unchanged['content_html'] == fragment
    metadata = edit(prose, boundary, {'semantic_style': 'heading2'}, operation='style_only')
    assert metadata['content_html'] == fragment and metadata['text_content'] == 'New rich prose'


@pytest.mark.parametrize('boundary', ['domain', 'chapter_http_owner'])
def test_committed_text_only_retry_keeps_generation_and_pointer(prose, boundary):
    domain, ids = prose[3:]
    revision = domain.project_generation(ids['project_id'], OWNER)
    first = edit(prose, boundary, {'text_content': 'Changed & saved'}, expected_revision=revision)
    retried = edit(prose, boundary, {'text_content': 'Changed & saved'}, expected_revision=revision)
    assert retried['generation'] == first['generation']
    assert retried['payload_object'] == first['payload_object']
    assert domain.project_generation(ids['project_id'], OWNER) == revision + 1
    with pytest.raises(DocumentConflict):
        domain.update('manuscript_block', ids, {'text_content': 'Stale edit'}, user_id=OWNER,
                      expected_generation=first['generation'] - 1)


@pytest.mark.parametrize('boundary', ['domain', 'chapter_http_owner'])
def test_image_partial_metadata_does_not_gain_prose_html(prose, boundary):
    prose[3].update('manuscript_block', prose[4], {'block_type': 'image', 'content_html': '',
        'text_content': '', 'image_path': '/api/assets/img_synthetic.png'}, user_id=OWNER)
    saved = edit(prose, boundary, {'text_content': 'Image metadata'})
    assert saved['content_html'] == '' and saved['image_path'] == '/api/assets/img_synthetic.png'


def test_text_only_undo_redo_restores_both_bodies_without_reusing_generation(prose):
    docs, objects, _, _, ids = prose
    domain = HostedDomainStore(ActionDocumentStore(docs), objects)
    journal = ActionJournal(domain.documents)
    token = journal.begin(ids['project_id'], OWNER)['token']
    with bind_action(token, ids['project_id'], OWNER):
        saved = domain.update('manuscript_block', ids, {'text_content': 'Changed & saved'}, user_id=OWNER)
    journal.finish(ids['project_id'], OWNER, token)
    journal.restore(ids['project_id'], OWNER, token, 'undo')
    undone = domain.get('manuscript_block', ids, user_id=OWNER)
    assert undone['content_html'] == '<strong>Old</strong> rich prose'
    assert undone['text_content'] == 'Old rich prose' and undone['generation'] > saved['generation']
    journal.restore(ids['project_id'], OWNER, token, 'redo')
    redone = domain.get('manuscript_block', ids, user_id=OWNER)
    assert redone['content_html'] == 'Changed &amp; saved' and redone['generation'] > undone['generation']


def test_denied_text_only_edit_does_not_read_or_publish_manuscript(prose, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unauthorized object access')
    monkeypatch.setattr(prose[1], 'read_chunks', forbidden)
    monkeypatch.setattr(prose[3], '_publish_hybrid', forbidden)
    with pytest.raises(ProjectUnavailable):
        prose[3].update('manuscript_block', prose[4], {'text_content': 'Denied'}, user_id=READER)


def test_private_text_only_replay_stays_encrypted_and_idempotent():
    from tests.test_v10518_sealed_recovery_source import setup_private, OWNER as PRIVATE_OWNER, STRANGER
    docs, objects, _, _, _, projects, pid = setup_private()
    domain = projects.domain
    ids = {'project_id': pid, 'block_id': 'b_1'}
    text = 'Synthetic private changed <text> & newline\nretained'
    revision = domain.project_generation(pid, PRIVATE_OWNER)
    saved = domain.update('manuscript_block', ids, {'text_content': text}, user_id=PRIVATE_OWNER,
                          operation='private_text_patch', expected_project_revision=revision)
    replay = domain.update('manuscript_block', ids, {'text_content': text}, user_id=PRIVATE_OWNER,
                           operation='private_text_patch', expected_project_revision=revision)
    assert replay == saved and saved['content_html'] == html_from_text(text)
    assert domain.project_generation(pid, PRIVATE_OWNER) == revision + 1
    assert text not in json.dumps(dict(docs.get(('projects', pid, 'blocks', 'b_1')).data))
    assert all(text.encode() not in b''.join(objects.read_chunks(row.name, max_bytes=8*1024*1024))
               for row in objects.list_prefix(f'projects/{pid}/private/'))
    with pytest.raises(ProjectUnavailable):
        domain.update('manuscript_block', ids, {'text_content': 'Denied'}, user_id=STRANGER)


def test_http_partial_chapter_replacement_replays_the_same_consistent_body(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hosted.idempotency import bind_operations, request_scope_key
    from hosted.runtime_services import _DEV_USER_ID
    from tests.test_v10364 import _replicas
    import hosted.http_api as http

    runtime, make, _, pid, cid = _replicas(monkeypatch)
    domain = runtime.projects.domain
    ids = {'project_id': pid, 'block_id': 'b_1'}
    domain.update('manuscript_block', ids, {'text_content': 'Original rich text',
                  'content_html': '<b>Original</b> rich text'}, user_id=_DEV_USER_ID)
    monkeypatch.setattr(http, 'runtime', runtime)
    app = FastAPI()
    app.include_router(http.product_router)
    prior = domain.project_generation(pid, _DEV_USER_ID)
    path = f'/api/chapters/{cid}/blocks'
    text = 'Edited <literal> & plain text\nfrom the real HTTP route'
    payload = {'blocks': [{'id': 'b_1', 'text_content': text}], 'expected_revision': prior}
    scope = request_scope_key('synthetic-partial-http', actor=_DEV_USER_ID, method='PUT', path=path)
    with TestClient(app) as client:
        with bind_operations(scope):
            first = client.put(path, headers={'x-breadmaker-project': pid}, json=payload)
        with bind_operations(scope):
            replay = client.put(path, headers={'x-breadmaker-project': pid}, json=payload)
    assert first.status_code == replay.status_code == 200, (first.text, replay.text)
    assert first.json()['ids'] == replay.json()['ids']
    assert first.json()['generation'] == replay.json()['generation'] == prior + 1
    fresh = make().projects.domain
    block = fresh.get('manuscript_block', ids, user_id=_DEV_USER_ID)
    assert block['text_content'] == text and block['content_html'] == html_from_text(text)
    assert fresh.project_generation(pid, _DEV_USER_ID) == prior + 1
