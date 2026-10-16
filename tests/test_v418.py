from source_contracts import backend_source, storage_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]

def test_v418_schema_and_ui_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'APP_SCHEMA_VERSION = 18' in (ROOT/'src'/'workspace'/'__init__.py').read_text()
    storage=storage_source(ROOT); js=frontend_source_from_app_path(ROOT/'static/app.js'); html=(ROOT/'static/index.html').read_text()
    assert 'ghost_title' not in storage
    assert 'position REAL NOT NULL DEFAULT 0' in storage
    assert 'description TEXT NOT NULL DEFAULT' in storage
    assert 'id="chaptersNavMode"' in html and 'id="scenesNavMode"' in html
    assert "manuscriptNavMode: 'chapters'" in js
    assert 'showModal(\'New Scene\'' not in js
    assert '/move-preview' not in backend_source(ROOT)
    assert '/api/scenes/{sid}/move' in backend_source(ROOT)

def test_v418_scene_creation_links_and_chapter_description(client,fresh_project):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    scene=assert_ok(client.post('/api/scenes',json={}))
    assert scene['title'].startswith('Scene ') and scene['block_ids']
    boot=assert_ok(client.get('/api/bootstrap')); row=next(s for s in boot['scenes'] if s['id']==scene['id'])
    assert row['block_count']==1 and row['first_chapter_id']==chapter['id']
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][-1]
    assert block['scene_id']==scene['id'] and block['text_content']==''
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'id':block['id'],'scene_id':scene['id'],'content_html':'Scene prose','text_content':'Scene prose'}]}))
    assert_ok(client.patch(f"/api/scenes/{scene['id']}",json={'title':'Bakery Argument','description':'Cinna pushes Eugene too hard.'}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Strengthen scene','status':'open'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'scene','target_id':str(scene['id'])}))
    assert assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))[0]['target_label']=='Bakery Argument'
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}",json={'description':'Development-only chapter summary.'}))
    assert assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['chapter']['description']=='Development-only chapter summary.'
    assert client.post(f"/api/scenes/{scene['id']}/attach",json={'block_ids':[block['id']]}).status_code==404
    assert client.post(f"/api/scenes/{scene['id']}/detach").status_code==404

def test_v418_deleting_chapter_removes_scene_identity_and_links(client,fresh_project):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'id':block['id'],'content_html':'Scene removed with chapter','text_content':'Scene removed with chapter'}]}))
    scene=assert_ok(client.post('/api/scenes',json={'title':'Remove Me','block_ids':[block['id']]}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Scene task','status':'open'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'scene','target_id':str(scene['id'])}))
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}"))
    boot2=assert_ok(client.get('/api/bootstrap')); assert all(s['id']!=scene['id'] for s in boot2['scenes'])
    assert assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))==[]

