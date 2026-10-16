from source_contracts import backend_source, storage_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]

def test_v4185_per_tab_scene_schema_and_ui_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'APP_SCHEMA_VERSION = 18' in (ROOT/'src'/'workspace'/'__init__.py').read_text()
    storage=storage_source(ROOT); app=backend_source(ROOT); js=frontend_source_from_app_path(ROOT/'static/app.js'); html=(ROOT/'static/index.html').read_text()
    assert 'tab_id INTEGER REFERENCES document_tabs(id) ON DELETE CASCADE' in storage
    assert 'class SceneCreate(BaseModel): title:str=""; description:str=""; block_ids:list[str]=[]; tab_id:Optional[int]=None' in app
    assert 'manuscript-tab-presentation' in js
    assert 'function manuscriptTabVisibility' in js and 'function setNavigatorVisibility' in js
    assert 'id="viewScenesBtn"' in html and '>Show Scenes<' in html
    assert 'sceneMarkersVisible()' in js


def test_v4185_created_scenes_are_owned_by_one_tab(client,fresh_project):
    boot=fresh_project['bootstrap']; primary=next(t for t in boot['tabs'] if t['is_manuscript'])
    notes=assert_ok(client.post('/api/documents',json={'title':'Notes','description':''}))
    notes_scene=assert_ok(client.post('/api/scenes',json={'title':'Notes scene','tab_id':notes['id']}))
    primary_scene=assert_ok(client.post('/api/scenes',json={'title':'Book scene','tab_id':primary['id']}))
    boot2=assert_ok(client.get('/api/bootstrap'))
    p=next(s for s in boot2['scenes'] if s['id']==notes_scene['id']); q=next(s for s in boot2['scenes'] if s['id']==primary_scene['id'])
    assert p['tab_id']==notes['id'] and p['first_tab_id']==notes['id'] and p['block_count']==1
    assert q['tab_id']==primary['id'] and q['first_tab_id']==primary['id'] and q['block_count']==1
    note_blocks=assert_ok(client.get(f"/api/chapters/{notes['chapter_id']}"))['blocks']
    assert any(b['scene_id']==notes_scene['id'] for b in note_blocks)
    primary_chapter=next(c for c in boot2['chapters'] if c['tab_id']==primary['id'])
    primary_blocks=assert_ok(client.get(f"/api/chapters/{primary_chapter['id']}"))['blocks']
    assert any(b['scene_id']==primary_scene['id'] for b in primary_blocks)
    assert client.post(f"/api/scenes/{notes_scene['id']}/detach").status_code==404

