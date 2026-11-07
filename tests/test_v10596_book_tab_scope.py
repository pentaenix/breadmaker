"""Full Text source-tab scope is identical for legacy and revision readers."""
import json

import pytest
from fastapi import HTTPException

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.action_journal import ActionDocumentStore
from hosted.domain_store import HostedDomainStore
from hosted.firestore.repositories import HostedProjectRepository
from hosted.manuscript_visibility import FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD, ManuscriptDocumentStore
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas
from tests.test_v10552 import request


def two_tabs(monkeypatch, mode):
    one, _, raw, pid, original_chapter = _replicas(monkeypatch)
    domain = one.projects.domain
    tab = domain.create('document_tab', {'project_id': pid},
        {'title': 'Appended', 'position': 1, 'kind': 'document'}, user_id=OWNER)
    chapter = domain.create('chapter', {'project_id': pid},
        {'tab_id': tab['id'], 'title': 'Appended chapter', 'position': 0}, user_id=OWNER)
    block = domain.create('manuscript_block', {'project_id': pid},
        {'tab_id': tab['id'], 'chapter_id': chapter['id'], 'position': 0,
         'content_html': '<p>Appended text.</p>', 'text_content': 'Appended text.'}, user_id=OWNER)
    domain.create('scene', {'project_id': pid}, {'tab_id': 'tab_manuscript',
        'chapter_id': original_chapter, 'title': 'Original scene'}, user_id=OWNER)
    scene = domain.create('scene', {'project_id': pid}, {'tab_id': tab['id'],
        'chapter_id': chapter['id'], 'title': 'Appended scene'}, user_id=OWNER)
    if mode != 'legacy':
        view = ManuscriptDocumentStore(AccountLifecycleDocumentStore(raw, actor=lambda: OWNER))
        documents = ActionDocumentStore(view)
        one.projects.documents = documents
        one.projects.authority = HostedProjectRepository(documents)
        one.projects.domain = HostedDomainStore(documents, one.projects.objects)
        if mode == 'revision':
            def enable(tx):
                root = ('projects', pid)
                tx.set(root, {**tx.get(root).data, FORMAT_FIELD: 1, 'status': 'active_manuscript_v1'})
                tab_path, chapter_path = root + ('tabs', tab['id']), root + ('chapters', chapter['id'])
                tx.set(tab_path, {**tx.get(tab_path).data, HEAD_FIELD: 'revision_append'})
                tx.set(chapter_path, {**tx.get(chapter_path).data, REVISION_FIELD: 'revision_append'})
            raw.run_transaction(enable)
    return one, raw, pid, tab, chapter, block, scene


@pytest.mark.parametrize('mode', ['legacy', 'legacy_view', 'revision'])
def test_collection_field_predicates_are_preserved_through_normal_and_bounded_views(monkeypatch, mode):
    one, _, pid, tab, chapter, block, _ = two_tabs(monkeypatch, mode)
    domain = one.projects.domain
    for family, entity, expected in [('chapters', 'chapter', chapter), ('blocks', 'manuscript_block', block)]:
        rows = domain.documents.run_transaction(lambda tx: tx.query_collection(
            ('projects', pid, family), field='tab_id', value=tab['id']))
        assert [row.path[-1] for row in rows] == [expected['id']]
        assert [row['id'] for row in domain.list(entity, {'project_id': pid}, user_id=OWNER,
            field='tab_id', value=tab['id'])] == [expected['id']]
        assert domain.list(entity, {'project_id': pid}, user_id=OWNER, field='tab_id', value='missing') == []


@pytest.mark.parametrize('mode', ['legacy', 'legacy_view', 'revision'])
@pytest.mark.parametrize('query', ['', '&manifest=true', '&bundled=true'])
def test_book_response_is_scoped_to_requested_tab_in_every_read_mode(monkeypatch, mode, query):
    import hosted.http_api as http
    one, _, pid, tab, chapter, block, scene = two_tabs(monkeypatch, mode)
    monkeypatch.setattr(http, 'runtime', one)
    response = http.hosted_book(request(pid, '/api/book', f"tab_id={tab['id']}{query}"))
    body = json.loads(response.body)
    assert [row['id'] for row in body['chapters']] == [chapter['id']]
    assert [row['id'] for row in body['scenes']] == [scene['id']]
    assert [row['id'] for row in body['blocks']] == ([block['id']] if not query else [])
    assert all(cid == chapter['id'] for cid in body['cache_refresh_chapters'])
    assert http.hosted_book(request(pid, '/api/book', f"tab_id={tab['id']}{query}", body['read_tag'])).status_code == 304
    with pytest.raises(HTTPException) as missing:
        http.hosted_book(request(pid, '/api/book', f'tab_id=missing{query}'))
    assert missing.value.status_code == 404


@pytest.mark.parametrize('mode', ['legacy', 'revision'])
def test_tabless_legacy_block_is_scoped_by_its_chapter_not_dropped(monkeypatch, mode):
    import hosted.http_api as http
    one, raw, pid, tab, chapter, block, _ = two_tabs(monkeypatch, mode)
    path = ('projects', pid, 'blocks', block['id'])
    data = dict(raw.get(path).data)
    data.pop('tab_id')
    raw.run_transaction(lambda tx: tx.set(path, data))
    monkeypatch.setattr(http, 'runtime', one)
    result = json.loads(http.hosted_book(request(pid, '/api/book', f"tab_id={tab['id']}")).body)
    assert [row['id'] for row in result['blocks']] == [block['id']]
    assert result['blocks'][0]['chapter_id'] == chapter['id']


@pytest.mark.parametrize('change', ['membership', 'generation'])
def test_scoped_book_preserves_final_authority_and_generation_fence(monkeypatch, change):
    import hosted.http_api as http
    import hosted.editing_packs as packs
    one, raw, pid, tab, _, _, _ = two_tabs(monkeypatch, 'revision')
    monkeypatch.setattr(http, 'runtime', one)
    native = packs.annotation_view
    def changed(*args, **kwargs):
        rows = native(*args, **kwargs)
        def mutate(tx):
            if change == 'membership':
                tx.delete(('projects', pid, 'members', OWNER))
            else:
                root = ('projects', pid)
                tx.set(root, {**tx.get(root).data, 'generation': tx.get(root).data['generation'] + 1})
        raw.run_transaction(mutate)
        return rows
    monkeypatch.setattr(packs, 'annotation_view', changed)
    with pytest.raises(HTTPException) as error:
        http.hosted_book(request(pid, '/api/book', f"tab_id={tab['id']}"))
    assert error.value.status_code == (404 if change == 'membership' else 409)
