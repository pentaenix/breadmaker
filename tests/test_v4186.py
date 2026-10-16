from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v4186_scene_model_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    app=backend_source(ROOT); js=frontend_source_from_app_path(ROOT/'static/app.js'); readme=(ROOT/'README.md').read_text()
    assert '@app.post("/api/scenes/{sid}/attach")' not in app
    assert '@app.post("/api/scenes/{sid}/detach")' not in app
    assert 'Unwritten Scene' not in js
    assert 'Attach selected prose' not in js and 'Detach from manuscript' not in js
    assert "await selectScene(scene.id,false)" in js
    assert 'single blank paragraph at the end of the active tab' in readme


def test_v4186_plus_scene_reuses_or_appends_one_blank_block(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=boot['chapters'][0]
    initial=assert_ok(client.get(f"/api/chapters/{ch['id']}"))['blocks']
    assert len(initial)==1 and initial[0]['text_content']=='' and initial[0]['scene_id'] is None
    scene1=assert_ok(client.post('/api/scenes',json={'tab_id':tab['id']}))
    blocks=assert_ok(client.get(f"/api/chapters/{ch['id']}"))['blocks']
    assert len(blocks)==1 and blocks[0]['id']==initial[0]['id'] and blocks[0]['scene_id']==scene1['id']
    scene2=assert_ok(client.post('/api/scenes',json={'tab_id':tab['id']}))
    blocks=assert_ok(client.get(f"/api/chapters/{ch['id']}"))['blocks']
    assert len(blocks)==2 and blocks[-1]['scene_id']==scene2['id'] and blocks[-1]['text_content']==''
    boot2=assert_ok(client.get('/api/bootstrap'))
    assert all(s['block_count']>=1 for s in boot2['scenes'])


def test_v4186_chapter_delete_removes_only_scenes_that_lose_all_prose(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=next(t for t in boot['tabs'] if t['is_manuscript']); first=boot['chapters'][0]
    second=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    ids1=assert_ok(client.put(f"/api/chapters/{first['id']}/blocks",json={'blocks':[{'text_content':'only scene','content_html':'only scene'},{'text_content':'cross start','content_html':'cross start'}]}))['ids']
    ids2=assert_ok(client.put(f"/api/chapters/{second['id']}/blocks",json={'blocks':[{'text_content':'cross end','content_html':'cross end'}]}))['ids']
    only=assert_ok(client.post('/api/scenes',json={'title':'Only first','block_ids':[ids1[0]]}))
    cross=assert_ok(client.post('/api/scenes',json={'title':'Cross','block_ids':[ids1[1],ids2[0]]}))
    assert_ok(client.delete(f"/api/chapters/{first['id']}"))
    scenes=assert_ok(client.get('/api/bootstrap'))['scenes']
    assert all(s['id']!=only['id'] for s in scenes)
    kept=next(s for s in scenes if s['id']==cross['id'])
    assert kept['block_count']==1 and kept['first_chapter_id']==second['id']
