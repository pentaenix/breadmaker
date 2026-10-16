from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _replace_blocks(client, chapter_id, blocks):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks', json={'blocks': blocks}))


def test_v4194_gap_exposes_blocks_and_bookmark_concrete_location(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    first=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    blocks=[
        {'id':first['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Scene words','text_content':'Scene words','image_path':'','scene_id':None},
        {'id':'gap-v4194','block_type':'paragraph','semantic_style':'body','content_html':'Outside words','text_content':'Outside words','image_path':'','scene_id':None},
    ]
    _replace_blocks(client,chapter['id'],blocks)
    assert_ok(client.post('/api/scenes',json={'block_ids':[first['id']]}))
    refreshed=assert_ok(client.get('/api/bootstrap'))
    gap=next(g for g in refreshed['scene_gaps'] if g['tab_id']==tab['id'])
    assert len(gap['block_ids'])==1
    gap_block_id=gap['block_ids'][0]
    bookmark=assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':gap_block_id,'end_block_id':gap_block_id,'start_offset':0,'end_offset':7}))['bookmark']
    assert bookmark['location_block_id']==gap_block_id
    assert bookmark['owner_scene_id'] is None


def test_v4194_structural_bookmarks_have_concrete_location(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    chapter_mark=assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))['bookmark']
    assert chapter_mark['location_block_id']==block['id']


def test_v4194_ui_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'function bookmarkShowsOnSceneGap(gap)' in js
    assert "gap.block_ids||[]" in js
    assert 'scene-gap-bookmark-icon' in js and 'scene-gap-bookmark-icon' in css
