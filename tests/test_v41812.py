from backend import data_access

from api.maps import anchors as maps_anchors
from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _set_paragraphs(client, chapter_id, values):
    data=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    existing=data['blocks'][0]
    payload=[]
    for i,text in enumerate(values):
        payload.append({
            'id': existing['id'] if i==0 else None,
            'block_type':'paragraph','semantic_style':'body',
            'content_html':text,'text_content':text,'image_path':'',
            'scene_id':None,
        })
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':payload}))
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']


def test_v41812_scene_can_extend_through_selection_that_already_contains_it(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    blocks=_set_paragraphs(client,chapter['id'],['one','two','three'])
    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[blocks[1]['id']]}))
    result=assert_ok(client.post(f"/api/scenes/{scene['id']}/extend",json={'block_ids':[blocks[1]['id'],blocks[2]['id']]}))
    assert result['added']==1
    current=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    assert current[0]['scene_id'] is None
    assert current[1]['scene_id']==scene['id']
    assert current[2]['scene_id']==scene['id']


def test_v41812_remove_from_scene_only_from_an_edge_and_deletes_identity_when_empty(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    blocks=_set_paragraphs(client,chapter['id'],['one','two','three'])
    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[b['id'] for b in blocks]}))
    middle=client.post(f"/api/scenes/{scene['id']}/remove-blocks",json={'block_ids':[blocks[1]['id']]})
    assert middle.status_code==409
    first=assert_ok(client.post(f"/api/scenes/{scene['id']}/remove-blocks",json={'block_ids':[blocks[0]['id']]}))
    assert first['scene_deleted'] is False
    current=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    assert current[0]['scene_id'] is None
    assert current[1]['scene_id']==scene['id'] and current[2]['scene_id']==scene['id']
    rest=assert_ok(client.post(f"/api/scenes/{scene['id']}/remove-blocks",json={'block_ids':[blocks[1]['id'],blocks[2]['id']]}))
    assert rest['scene_deleted'] is True
    assert client.get(f"/api/scenes/{scene['id']}/manuscript").status_code==404
    current=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    assert all(block['scene_id'] is None for block in current)


def test_v41812_deleting_last_real_chapter_leaves_hidden_writable_container(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}/permanent"))
    boot=assert_ok(client.get('/api/bootstrap'))
    rows=[c for c in boot['chapters'] if c['tab_id']==tab['id']]
    assert len(rows)==1 and rows[0]['is_implicit']==1
    book=assert_ok(client.get(f"/api/book?tab_id={tab['id']}"))
    assert len(book['chapters'])==1 and book['chapters'][0]['is_implicit']==1
    assert len(book['blocks'])==1 and book['blocks'][0]['semantic_style']=='body'
    assert book['blocks'][0]['text_content']==''


def test_v41812_frontend_scene_focus_and_chapterless_contract():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    storage=storage_source(ROOT)
    assert "updateManuscriptTabPrefs({sceneMarkers:true})" in js
    assert "if(state.viewMode==='scene'||state.viewMode==='scene-gap'){await selectScene(sceneId);return}" in js
    assert "row.ondblclick=e=>{clearTimeout(sceneNavClickTimer)" in js
    assert "state.viewMode==='scene-gap'" in js and "function renderSceneGapEditor()" in js
    assert "chapterMarkersVisible()&&ch&&!ch.is_implicit" in js
    assert "function visibleChapters(docId=null){return state.chapters.filter(ch=>!ch.is_implicit" in js
    assert "manuscriptTabChapterRows(docId=state.activeDocumentId){return visibleChapters(docId)}" in js
    assert '.book-nav.collapsed .scene-outline-main{flex:1 1 auto' in css
    assert '.scene-gap-hidden{display:none!important}' in css
    assert 'is_implicit INTEGER NOT NULL DEFAULT 0' in storage


def test_v41812_selection_scene_action_is_contextual():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    segment=js[js.index('function sceneSelectionPlan'):js.index('async function sceneReferenceRows')]
    assert "kind:'create',label:'Scene'" in segment
    assert "kind:'extend'" in segment and "label:'Add to Scene'" in segment
    assert "kind:'remove'" in segment and "label:'Remove from Scene'" in segment
    assert '/extend' in segment and '/remove-blocks' in segment


def test_v41812_release_and_hidden_chapter_source_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'AND c.is_implicit=0' in app
    assert '### 4.18.12 Scene range editing and chapterless writing' in readme
    assert '### 4.18.12 Scene-range and chapterless-manuscript contract' in architecture


def test_v41812_implicit_chapter_is_not_counted_or_exported_as_structure(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}/permanent"))
    metadata=assert_ok(client.get('/api/project/metadata'))
    assert metadata['manuscript']['chapters']==0
    canvas_catalog=assert_ok(client.get('/api/canvas-populate'))
    chapter_templates=[tpl for group in canvas_catalog['categories'] for tpl in group['templates'] if tpl.get('source')=='project.chapters']
    assert chapter_templates and all(tpl['item_count']==0 for tpl in chapter_templates)
    map_catalog=assert_ok(client.get('/api/map-populate'))
    chapter_templates=[tpl for group in map_catalog['categories'] for tpl in group['templates'] if tpl.get('source')=='project.chapters']
    assert chapter_templates and all(tpl['item_count']==0 for tpl in chapter_templates)


def test_v41812_real_chapters_can_reorder_around_hidden_container(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    original=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    assert_ok(client.delete(f"/api/chapters/{original['id']}/permanent"))
    first=assert_ok(client.post('/api/chapters',json={'title':'Visible A','tab_id':tab['id']}))
    second=assert_ok(client.post('/api/chapters',json={'title':'Visible B','tab_id':tab['id']}))
    assert_ok(client.post('/api/chapters/reorder',json={'ids':[second['id'],first['id']]}))
    boot=assert_ok(client.get('/api/bootstrap'))
    rows=sorted([c for c in boot['chapters'] if c['tab_id']==tab['id']],key=lambda c:(c['position'],c['id']))
    assert rows[0]['is_implicit']==1
    assert [c['title'] for c in rows if not c['is_implicit']]==['Visible B','Visible A']


def test_v41812_hidden_container_does_not_leak_into_developmental_canvas_or_map_axis(client,fresh_project,app_module_fixture):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}/permanent"))
    boot=assert_ok(client.get('/api/bootstrap'))
    implicit=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and c['is_implicit'])
    saved=assert_ok(client.put(f"/api/chapters/{implicit['id']}/blocks",json={'blocks':[{
        'block_type':'paragraph','semantic_style':'body','content_html':'Chapterless prose','text_content':'Chapterless prose'
    }]}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    canvas_data=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert not [node for node in canvas_data['nodes'] if node['node_type']=='chapter']
    with app_module_fixture.storage.tx() as con:
        pid=data_access.project(con)['id']
        chronology=maps_anchors._manuscript_chronology(con,pid)
    assert chronology['chapters']==[]
    assert saved['ids'][0] in chronology['block_positions']
    assert chronology['total_words']>0


def test_v41812_chapterless_docx_and_location_labels_do_not_invent_a_chapter(client,fresh_project):
    import io
    from docx import Document

    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}/permanent"))
    boot=assert_ok(client.get('/api/bootstrap'))
    implicit=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and c['is_implicit'])
    saved=assert_ok(client.put(f"/api/chapters/{implicit['id']}/blocks",json={'blocks':[{
        'block_type':'paragraph','semantic_style':'body','content_html':'Continuous manuscript text','text_content':'Continuous manuscript text'
    }]}))
    block_id=saved['ids'][0]
    comment=assert_ok(client.post('/api/comments',json={'body':'A note','items':[{
        'block_id':block_id,'start_offset':0,'end_offset':10,'selected_text':'Continuous','prefix_context':'','suffix_context':' manuscript text'
    }]}))
    assert comment['chapter_title']==''

    exported=client.get('/api/export/docx')
    assert exported.status_code==200
    doc=Document(io.BytesIO(exported.content))
    rows=[(p.text,p.style.name) for p in doc.paragraphs if p.text]
    assert ('Continuous manuscript text','Normal') in rows
    assert not any(style=='Heading 1' for _,style in rows)


def test_v41812_pdf_export_source_skips_implicit_chapter_heading():
    app=backend_source(ROOT)
    assert 'SELECT id,position,title,is_implicit FROM chapters' in app
    pdf=app[app.index('def _pdf_export_book'):app.index('@app.post("/api/export/pdf")')]
    assert "if not ch.get('is_implicit')" in pdf
    assert "if ch.get('is_implicit') and not any" in pdf
