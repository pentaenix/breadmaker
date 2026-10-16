from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _replace_blocks(client, chapter_id, blocks):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks', json={'blocks': blocks}))


def test_v4193_structural_bookmarks_project_to_both_outlines(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    _replace_blocks(client,chapter['id'],[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'scene start','text_content':'scene start','image_path':'','scene_id':None}])
    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[block['id']]}))

    chapter_mark=assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))['bookmark']
    assert chapter_mark['owner_chapter_id']==chapter['id']
    assert chapter_mark['owner_scene_id']==scene['id']

    scene_mark=assert_ok(client.put('/api/bookmark',json={'target_type':'scene','scene_id':scene['id']}))['bookmark']
    assert scene_mark['owner_scene_id']==scene['id']
    assert scene_mark['owner_chapter_id']==chapter['id']


def test_v4193_bookmark_move_recomputes_structural_owners(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    ch1=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    b1=assert_ok(client.get(f"/api/chapters/{ch1['id']}"))['blocks'][0]
    _replace_blocks(client,ch1['id'],[{'id':b1['id'],'block_type':'paragraph','semantic_style':'body','content_html':'first scene','text_content':'first scene','image_path':'','scene_id':None}])
    s1=assert_ok(client.post('/api/scenes',json={'block_ids':[b1['id']]}))

    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    b2=assert_ok(client.get(f"/api/chapters/{ch2['id']}"))['blocks'][0]
    _replace_blocks(client,ch2['id'],[{'id':b2['id'],'block_type':'paragraph','semantic_style':'body','content_html':'second scene','text_content':'second scene','image_path':'','scene_id':None}])
    s2=assert_ok(client.post('/api/scenes',json={'block_ids':[b2['id']]}))

    first=assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':b1['id'],'end_block_id':b1['id'],'start_offset':0,'end_offset':5}))['bookmark']
    assert (first['owner_chapter_id'],first['owner_scene_id'])==(ch1['id'],s1['id'])
    second=assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':b2['id'],'end_block_id':b2['id'],'start_offset':0,'end_offset':6}))['bookmark']
    assert (second['owner_chapter_id'],second['owner_scene_id'])==(ch2['id'],s2['id'])


def test_v4193_ui_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert "b.owner_scene_id!=null" in js and "b.owner_chapter_id!=null" in js
    assert '<span aria-hidden="true">☰</span>' in html
    assert '.nav-collapse-button>span{display:block;line-height:1;transform:translateY(-1px)}' in css
