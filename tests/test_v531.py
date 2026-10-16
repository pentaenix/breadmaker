from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _blocks(client, chapter_id):
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']


def _set_text(client, chapter_id, text):
    block=_blocks(client,chapter_id)[0]
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{
        'id':block['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':text,'text_content':text,'image_path':'','scene_id':block.get('scene_id'),
    }]}))
    return _blocks(client,chapter_id)[0]


def test_v531_apply_preview_uses_exact_rollback_path_and_does_not_mutate_current(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    first=_set_text(client,ch1['id'],'First scene')
    second=_set_text(client,ch2['id'],'Second scene')
    scene1=assert_ok(client.post('/api/scenes',json={'title':'First','block_ids':[first['id']]}))
    scene2=assert_ok(client.post('/api/scenes',json={'title':'Second','block_ids':[second['id']]}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental','developmental_scope':'scenes'}))
    nodes=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes']
    first_node=next(n for n in nodes if n['scene_id']==scene1['id'])
    second_node=next(n for n in nodes if n['scene_id']==scene2['id'])
    order=[second_node['id'],first_node['id']]

    preview=assert_ok(client.post('/api/developmental/preflight',json={'ordered_node_ids':order}))
    assert preview['can_apply'] is True and preview['has_changes'] is True
    assert f"scene:{scene1['id']}" in preview['changed_keys'] or f"scene:{scene2['id']}" in preview['changed_keys']
    current_ch1=next(ch for ch in preview['current']['chapters'] if ch['id']==ch1['id'])
    proposed_ch1=next(ch for ch in preview['proposed']['chapters'] if ch['id']==ch1['id'])
    assert [s['id'] for s in current_ch1['scenes']]==[scene1['id']]
    assert [s['id'] for s in proposed_ch1['scenes']]==[scene2['id']]

    # Preflight is rollback-only: Current is untouched until the writer confirms Apply.
    assert _blocks(client,ch1['id'])[0]['scene_id']==scene1['id']
    assert _blocks(client,ch2['id'])[0]['scene_id']==scene2['id']

    assert_ok(client.post('/api/developmental/apply',json={'ordered_node_ids':order}))
    assert any(b['scene_id']==scene2['id'] for b in _blocks(client,ch1['id']))


def test_v531_canvas_apply_surface_is_conditional_and_preview_first():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'aria-label="Structure"' in html
    assert 'id="canvasStructureBtn"' in html
    assert 'id="reviewStructureChanges"' in js and '>Review changes<' in js
    assert "!developmentalStructureCards().length" in js
    assert "api('/api/developmental/preflight'" in js
    assert "showModal('Apply to Manuscript'" in js
    assert 'Current manuscript' in js and 'Canvas order' in js
    assert 'developmental-order-compare' in css
