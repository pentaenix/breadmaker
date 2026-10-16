from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_4173_selection_cleanup_and_find_scope_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert "function syncFindScopeLabel()" in js
    assert "label.textContent=state.viewMode==='book'?'Book':state.viewMode==='scene'?'Scene':state.viewMode==='scene-gap'?'Tab':'Chapter'" in js
    assert "Suggest rewrite…" not in js
    assert "Add editorial note…" not in js
    assert "function suggestSelection" not in js
    assert "function noteSelection" not in js
    assert "Linked manuscript passage" in js
    assert "task-linked-selection" in css
    assert "openManuscriptRangeReference" in js
    assert "breadmaker-linked-range" in js


def test_4173_manuscript_reference_lists_navigation_context(client, fresh_project):
    boot=fresh_project['bootstrap']
    chapter=boot['chapters'][0]
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    block=data['blocks'][0]
    # Ensure there is stable content in the referenced block.
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{
        'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Alpha beta gamma','text_content':'Alpha beta gamma','image_path':'','scene_id':None,
    }]}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Linked selection','description':'Full task editor','status':'open'}))
    ref=assert_ok(client.post('/api/references',json={
        'source_type':'task','source_id':str(task['id']),'target_type':'manuscript_range','target_id':'range',
        'start_block_id':block['id'],'end_block_id':block['id'],'start_offset':6,'end_offset':10,'excerpt':'beta',
    }))
    refs=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))
    row=next(r for r in refs if r['id']==ref['id'])
    assert row['target_label']=='beta'
    assert row['target_start_chapter_id']==chapter['id']
    assert row['target_end_chapter_id']==chapter['id']
    assert row['target_missing'] is False
