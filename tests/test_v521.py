from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _active_workspace(client):
    ws=assert_ok(client.get('/api/workspace'))
    return ws, ws['active_project_id']


def test_v521_import_readiness_only_treats_default_blank_manuscript_as_empty(client, fresh_project):
    ready=assert_ok(client.get('/api/import/readiness'))
    assert ready['manuscript_empty'] is True
    chapter=fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'text_content':'Existing prose','content_html':'Existing prose','semantic_style':'body','block_type':'paragraph'}]}))
    ready=assert_ok(client.get('/api/import/readiness'))
    assert ready['manuscript_empty'] is False
    assert ready['reason']=='manuscript-content'


def test_v521_import_readiness_is_conservative_about_editorial_structure(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))
    ready=assert_ok(client.get('/api/import/readiness'))
    assert ready['manuscript_empty'] is False
    assert ready['reason']=='bookmark'


def test_v521_create_new_project_from_manuscript_keeps_old_project_untouched(client, fresh_project):
    old_ws,old_id=_active_workspace(client)
    old_chapter=fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{old_chapter['id']}/blocks",json={'blocks':[{'text_content':'Keep this old project','content_html':'Keep this old project','semantic_style':'body','block_type':'paragraph'}]}))
    result=assert_ok(client.post('/api/import/new-project?chapter_heading=heading1',files={'file':('Imported Novel.txt',b'New project manuscript','text/plain')}))
    assert result['reload'] is True
    new_ws,new_id=_active_workspace(client)
    assert new_id!=old_id
    active=next(p for p in new_ws['projects'] if p['id']==new_id)
    assert active['name']=='Imported Novel'
    boot=assert_ok(client.get('/api/bootstrap'))
    main=next(t for t in boot['tabs'] if t['is_manuscript'])
    book=assert_ok(client.get(f"/api/book?tab_id={main['id']}"))
    assert any('New project manuscript' in str(b.get('text_content') or '') for b in book['blocks'])

    # Switching back proves the original project was never replaced.
    assert_ok(client.post(f'/api/projects/{old_id}/activate'))
    old_boot=assert_ok(client.get('/api/bootstrap'))
    old_main=next(t for t in old_boot['tabs'] if t['is_manuscript'])
    old_book=assert_ok(client.get(f"/api/book?tab_id={old_main['id']}"))
    assert any('Keep this old project' in str(b.get('text_content') or '') for b in old_book['blocks'])


def test_v521_invalid_new_project_import_does_not_create_or_switch_projects(client, fresh_project):
    before,before_id=_active_workspace(client)
    before_ids={p['id'] for p in before['projects']}
    response=client.post('/api/import/new-project',files={'file':('not-supported.pdf',b'bad','application/pdf')})
    assert response.status_code==400
    after,after_id=_active_workspace(client)
    assert after_id==before_id
    assert {p['id'] for p in after['projects']}==before_ids


def test_v521_release_contract_has_context_aware_import_ui():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    for token in ('/api/import/readiness','importDestinationReplace','importDestinationNew','importDestinationTabs','/api/import/new-project'):
        assert token in js
    assert 'Replace Main Manuscript' in js
    assert 'Append as Extra Tabs' in js
