from source_contracts import frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _set_blocks(client,chapter_id,texts):
    data=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    first=data['blocks'][0]
    payload=[]
    for i,text in enumerate(texts):
        payload.append({'id':first['id'] if i==0 else None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None})
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':payload}))
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']


def test_v41813_add_chapter_can_insert_after_focused_anchor(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    first=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    tail=assert_ok(client.post('/api/chapters',json={'title':'Tail','tab_id':tab['id']}))
    middle=assert_ok(client.post('/api/chapters',json={'title':'Middle','tab_id':tab['id'],'after_chapter_id':first['id']}))
    boot=assert_ok(client.get('/api/bootstrap'))
    rows=sorted([c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c['is_implicit']],key=lambda c:(c['position'],c['id']))
    assert [c['id'] for c in rows]==[first['id'],middle['id'],tail['id']]


def test_v41813_add_scene_after_anchor_inserts_blank_prose_at_scene_boundary(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    blocks=_set_blocks(client,chapter['id'],['scene one','outside','scene two'])
    first=assert_ok(client.post('/api/scenes',json={'title':'First','block_ids':[blocks[0]['id']]}))
    second=assert_ok(client.post('/api/scenes',json={'title':'Second','block_ids':[blocks[2]['id']]}))
    inserted=assert_ok(client.post('/api/scenes',json={'tab_id':tab['id'],'after_scene_id':first['id']}))
    current=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    assert [b['text_content'] for b in current]==['scene one','','outside','scene two']
    assert current[1]['scene_id']==inserted['id']
    boot=assert_ok(client.get('/api/bootstrap'))
    scenes=sorted([s for s in boot['scenes'] if s['tab_id']==tab['id']],key=lambda s:(s['position'],s['id']))
    assert [s['id'] for s in scenes]==[first['id'],inserted['id'],second['id']]


def test_v41813_scene_selection_remove_is_in_more_menu_and_hover_is_standardized():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    segment=js[js.index('function selectionMoreItems'):js.index('// -------------------- markers / annotations')]
    assert "offerRemove=scenePlan.kind==='extend'&&scenePlan.canRemove" in segment
    assert "label:'Remove from Scene',action:()=>createSceneFromSelection(items,{forceRemove:true})" in segment
    assert "button.textContent=plan.label||'Scene'" in segment
    assert "function queueSceneDetailsHover(scene,row)" in js
    assert "className='chapter-details-hover-card scene-details-hover-card'" in js
    assert "row.onmouseenter=()=>queueSceneDetailsHover(scene,row)" in js


def test_v41813_gap_flyer_is_cleared_when_navigator_changes():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "if(!target.isConnected||!target.matches(':hover'))return" in js
    assert "function renderSceneTree(tree){\n  hideSceneGapHover();hideSceneDetailsHover();" in js
    assert "function renderChapterTree(){\n  hideSceneGapHover();hideSceneDetailsHover();" in js


def test_v41813_focused_add_actions_send_anchor_ids():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "afterSceneId=state.viewMode==='scene'" in js
    assert 'after_scene_id:afterSceneId' in js
    assert "afterChapterId=state.viewMode==='chapter'" in js
    assert 'after_chapter_id:afterChapterId' in js


def test_v41813_release_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '### 4.18.13 Scene polish and focused insertion' in readme
    assert '### 4.18.13 Scene interaction and insertion contract' in architecture
