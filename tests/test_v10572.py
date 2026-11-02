"""Real portable exports of fresh/stale hosted Highlights retain their anchors."""
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.editing_search import HostedEditingSearch
from hosted.local_portability import LocalPortabilityError, to_local_package
from hosted.packages import HostedProjectPackageService
from tests.test_v10353 import _services, OWNER
from workspace import Workspace


def highlighted():
    docs, _, _, _, projects = _services()
    pid = projects.create('Disposable portable Highlights')['id']
    domain = projects.domain
    block = domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)[0]
    domain.update('manuscript_block', {'project_id': pid, 'block_id': block['id']},
        {'text_content': 'She walked quietly and softly.',
         'content_html': '<p>She walked quietly and softly.</p>'}, user_id=OWNER)
    domain.documents = ActionDocumentStore(docs)
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER)['token']
    with bind_action(token, pid, OWNER):
        result = HostedEditingSearch(domain).run(pid, OWNER, {'scan': 'adverbs', 'tab_id': block['tab_id']})
    journal.finish(pid, OWNER, token)
    assert result['matches'] == 2
    return HostedProjectPackageService(projects), pid, block['id']


def reopened(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        raw = archive.read('project.sqlite3')
    db = sqlite3.connect(':memory:')
    db.deserialize(raw)
    assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    return db


def test_generated_highlights_export_as_real_local_package_and_restore_as_new_project(tmp_path):
    service, pid, _ = highlighted()
    payload = service.export_local_bytes(pid, OWNER)
    with reopened(payload) as db:
        assert db.execute('SELECT selected_text,stale FROM annotations ORDER BY start_offset').fetchall() == [
            ('quietly', 0), ('softly', 0)]
        assert db.execute('SELECT body FROM annotations').fetchall() == [('editing-search/v1:adverbs',)] * 2
    path = tmp_path / 'synthetic.breadmaker'
    path.write_bytes(payload)
    local = Workspace(tmp_path / 'local')
    local.init()
    assert local.verify_package(path)['ok']
    native = local.import_project(path)
    assert native['id'] != pid
    with sqlite3.connect(Path(native['path']) / 'project.sqlite3') as db:
        registry = json.loads(db.execute("SELECT value_json FROM ui_state WHERE key='editing-search-markers/v1'").fetchone()[0])
        marker = db.execute('SELECT id,created_at FROM markup_labels').fetchone()
        assert registry['adverbs'] == dict(id=marker[0], created_at=marker[1])
    restored = service.import_bytes(payload, OWNER)
    assert restored['id'] != pid
    annotations = service.domain.list('annotation', {'project_id': restored['id']}, user_id=OWNER)
    assert {row['selected_text'] for row in annotations} == {'quietly', 'softly'}
    assert not any(row['stale'] for row in annotations)
    domain = service.domain
    rid = restored['id']
    tab = domain.list('document_tab', {'project_id': rid}, user_id=OWNER)[0]['id']
    journal = ActionJournal(domain.documents)
    token = journal.begin(rid, OWNER)['token']
    with bind_action(token, rid, OWNER):
        assert HostedEditingSearch(domain).run(rid, OWNER, {'scan': 'adverbs', 'action': 'recheck', 'tab_id': tab})['matches'] == 2
    journal.finish(rid, OWNER, token)
    journal.restore(rid, OWNER, token, 'undo')
    assert {row['id'] for row in domain.list('annotation', {'project_id': rid}, user_id=OWNER)} == {row['id'] for row in annotations}


def test_changed_source_preserves_stale_highlights_and_current_text_in_portable_export():
    service, pid, bid = highlighted()
    service.domain.update('manuscript_block', {'project_id': pid, 'block_id': bid},
        {'text_content': 'She answered clearly.', 'content_html': '<p>She answered clearly.</p>'}, user_id=OWNER)
    payload = service.export_local_bytes(pid, OWNER)
    with reopened(payload) as db:
        assert db.execute('SELECT text_content FROM manuscript_blocks').fetchall() == [('She answered clearly.',)]
        assert db.execute('SELECT selected_text,stale FROM annotations ORDER BY start_offset').fetchall() == [
            ('quietly', 1), ('softly', 1)]
    restored = service.import_bytes(payload, OWNER)
    ranges = service.domain.list('annotation', {'project_id': restored['id']}, user_id=OWNER)
    assert len(ranges) == 2 and all(row['stale'] for row in ranges)


def test_annotation_projection_rejects_corrupt_provenance_and_unknown_product_fields():
    service, pid, _ = highlighted()
    source = service.export_bytes(pid, OWNER)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    for key, value, error in [('source_generation', 'not-a-generation', 'ANNOTATION_SOURCE_INVALID'),
                              ('unknown_product_field', 'must not silently disappear', 'UNREPRESENTABLE_FIELD')]:
        document = json.loads(members['project.json'])
        document['entities']['annotation'][0]['data'][key] = value
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, raw in members.items():
                archive.writestr(name, json.dumps(document).encode() if name == 'project.json' else raw)
        with pytest.raises(LocalPortabilityError, match=error):
            to_local_package(output.getvalue(), project_generation=1)


def test_renamed_cleared_marker_keeps_category_ownership_through_native_and_hosted_restore():
    service, pid, bid = highlighted()
    domain = service.domain
    label = domain.list('markup_label', {'project_id': pid}, user_id=OWNER)[0]
    domain.update('markup_label', {'project_id': pid, 'label_id': label['id']},
                  {'name': 'My renamed marker', 'description': 'My retained description'}, user_id=OWNER)
    block = domain.get('manuscript_block', {'project_id': pid, 'block_id': bid}, user_id=OWNER)
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER)['token']
    with bind_action(token, pid, OWNER):
        HostedEditingSearch(domain).run(pid, OWNER, {'scan': 'adverbs', 'action': 'clear', 'tab_id': block['tab_id']})
    journal.finish(pid, OWNER, token)
    payload = service.export_local_bytes(pid, OWNER)
    with reopened(payload) as db:
        assert db.execute('SELECT COUNT(*) FROM annotations').fetchone() == (0,)
        marker = db.execute('SELECT id,created_at,name,description FROM markup_labels').fetchone()
        registry = json.loads(db.execute("SELECT value_json FROM ui_state WHERE key='editing-search-markers/v1'").fetchone()[0])
        assert registry['adverbs'] == dict(id=marker[0], created_at=marker[1])
        assert marker[2:] == ('My renamed marker', 'My retained description')
    restored = service.import_bytes(payload, OWNER)
    rid = restored['id']
    markers = domain.list('markup_label', {'project_id': rid}, user_id=OWNER)
    assert len(markers) == 1 and markers[0]['editing_search_categories'] == ['adverbs']
    tab = domain.list('document_tab', {'project_id': rid}, user_id=OWNER)[0]['id']
    assert HostedEditingSearch(domain).status(rid, OWNER, tab)['searches']['adverbs']['marker']['id'] == markers[0]['id']


def test_large_native_roundtrip_restages_generated_results_as_bounded_packs():
    service, pid, bid = highlighted()
    domain = service.domain
    text = 'quietly ' * 600
    domain.update('manuscript_block', {'project_id': pid, 'block_id': bid},
                  {'text_content': text, 'content_html': text}, user_id=OWNER)
    block = domain.get('manuscript_block', {'project_id': pid, 'block_id': bid}, user_id=OWNER)
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER)['token']
    with bind_action(token, pid, OWNER):
        HostedEditingSearch(domain).run(pid, OWNER, {'scan': 'adverbs', 'action': 'recheck', 'tab_id': block['tab_id']})
    journal.finish(pid, OWNER, token)
    restored = service.import_bytes(service.export_local_bytes(pid, OWNER), OWNER)
    rid = restored['id']
    assert len(service.documents.query_collection(('projects', rid, 'annotations'))) == 1
    ranges = domain.list('annotation', {'project_id': rid}, user_id=OWNER)
    assert len(ranges) == 600 and not any(row['stale'] for row in ranges)
    assert domain.list('manuscript_block', {'project_id': rid}, user_id=OWNER)[0]['text_content'] == text


def test_portable_registry_cannot_silently_drop_unknown_categories_or_bind_reused_marker_id():
    from hosted.portability_editing import native_categories, native_annotation_ids, project_row
    for kinds in (['unimplemented-category'], ['adverbs', 'adverbs']):
        with pytest.raises(ValueError, match='EDITING_REGISTRY_INVALID'):
            project_row('markup_labels', dict(editing_search_categories=kinds), 1, 'created', {})
    service, pid, _ = highlighted()
    with reopened(service.export_local_bytes(pid, OWNER)) as db:
        assert native_categories(db) == {'1': ['adverbs']}
        db.execute('UPDATE markup_labels SET created_at=?', ('a-new-identity-reusing-the-id',))
        assert native_categories(db) == {}
    assert native_annotation_ids('synthetic', {'annotation': [dict(id=1, annotation_type='highlight', body='adverbs')]}) == {}
