from source_contracts import frontend_source_from_app_path
from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v4178_find_is_toggle_and_selection_toolbar_requires_actionable_prose():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "$('#findBtn').onclick=()=>$('#findPopover').classList.contains('hidden')?openFind():closeFind()" in js
    assert "if(!state.selectionToolbarItems.length){state.selectionToolbarRange=null;hideSelectionToolbar();return}" in js


def test_v4178_cross_tab_checkpoint_apply_with_note_clones_nested_state(fresh_project,client):
    boot=fresh_project['bootstrap']; source=boot['chapters'][0]
    book=assert_ok(client.get('/api/book')); source_block=book['blocks'][0]
    text='Hello checkpoint from the source chapter.'
    assert_ok(client.put(f"/api/chapters/{source['id']}/blocks",json={'blocks':[{
        'id':source_block['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':text,'text_content':text,'image_path':'','scene_id':None,
    }]}))
    source_scene=assert_ok(client.post('/api/scenes',json={'title':'Source Scene','description':'scene metadata','block_ids':[source_block['id']]}))
    item={'block_id':source_block['id'],'start_offset':0,'end_offset':5,'selected_text':'Hello','prefix_context':'','suffix_context':' checkpoint'}
    source_note=assert_ok(client.post('/api/comments',json={'body':'Keep this thought','items':[item]}))
    assert_ok(client.post(f"/api/comments/{source_note['id']}/messages",json={'body':'Follow-up thought'}))
    checkpoint=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':source['id'],'name':'Source version'}))

    other=assert_ok(client.post('/api/documents',json={'title':'Other Tab'})); target_id=other['chapter_id']
    preflight=assert_ok(client.post(f"/api/checkpoints/{checkpoint['id']}/preflight",json={'target_chapter_id':target_id}))
    assert preflight['requires_confirmation']
    assert any(item['kind']=='scene' and item['from']=='Source Scene' for item in preflight['renames'])
    token=assert_ok(client.post('/api/undo/begin?label=Apply%20cross-tab%20checkpoint'))['token']
    applied=assert_ok(client.post(f"/api/checkpoints/{checkpoint['id']}/apply",json={'target_chapter_id':target_id,'confirm_conflicts':True,'preflight_token':preflight['preflight_token']}))
    assert applied['chapter_id']==target_id and applied['mode']=='replace_chapter'
    assert assert_ok(client.post(f'/api/undo/finish/{token}'))['changed']>0

    other_book=assert_ok(client.get(f"/api/book?tab_id={other['id']}"))
    assert other_book['chapters'][0]['title']=='Other Tab', 'Applying across tabs preserves the live target chapter identity.'
    assert other_book['blocks'][0]['text_content']==text
    target_scene_id=other_book['blocks'][0]['scene_id']
    assert target_scene_id and target_scene_id!=source_scene['id'], 'A copied chapter must not share its source Scene identity across tabs.'

    notes=assert_ok(client.get('/api/comments?status=all'))
    source_threads=[n for n in notes if any(a['chapter_id']==source['id'] for a in n['anchors'])]
    target_threads=[n for n in notes if any(a['chapter_id']==target_id for a in n['anchors'])]
    assert len(source_threads)==1 and len(target_threads)==1
    assert source_threads[0]['id']!=target_threads[0]['id'], 'Cross-chapter Apply clones Note threads instead of mutating/reusing the source thread.'
    assert [m['body'] for m in source_threads[0]['messages']]==['Keep this thought','Follow-up thought']
    assert [m['body'] for m in target_threads[0]['messages']]==['Keep this thought','Follow-up thought']
