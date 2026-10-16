from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v4187_static_manuscript_entity_menu_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(ROOT/'static/app.js'); css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css')); app=backend_source(ROOT)
    assert "{label:'Split View',action:()=>openChapterSplit(ch.id)},{label:'Details',action:()=>configureChapterDetails(ch)}" in js
    assert "{label:'Cut',danger:true,tooltip:'Move this Chapter and all of its contents to Cuts.'" in js
    assert "{label:'Delete',danger:true,tooltip:'Delete this Chapter and all of its contents without creating a Cut.'" in js
    assert "{label:'Details',tooltip:'Edit the Scene name and details, or manage its links.'" in js
    assert "{label:'Cut',danger:true,tooltip:'Move this Scene’s manuscript contents to Cuts and remove the Scene.'" in js
    assert "{label:'Delete',danger:true,tooltip:'Remove the Scene and its links without changing the manuscript text.'" in js
    assert 'Open Scene' not in js and 'Go to manuscript' not in js
    assert "function markerMenuItems(id){return[{label:'Navigate'" in js and "{label:'Details',action:()=>editMarker(id)}" in js
    assert "function checkpointMenuItems(cp){return[{label:'Compare'" in js and "{label:'Details',action:()=>openCheckpointDetails(cp)}" in js
    assert "function commentMenuItems(thread){return[{label:thread.status==='resolved'?'Reopen':'Resolve'" in js
    assert 'row.oncontextmenu=e=>{e.preventDefault();openMarkerMenu' in js
    assert 'row.oncontextmenu=e=>{e.preventDefault();openCheckpointMenu' in js
    assert 'row.oncontextmenu=e=>{e.preventDefault();openCommentMenu' in js
    assert 'chapter-details-hover-card' in css and 'context-menu-hover-tip' in css
    assert '@app.post("/api/scenes/{sid}/cut")' in app
    assert '@app.delete("/api/chapters/{cid}/permanent")' in app


def test_v4187_scene_cut_moves_prose_to_cuts_and_removes_scene(client,fresh_project):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    ids=assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[
        {'text_content':'First scene paragraph','content_html':'First scene paragraph'},
        {'text_content':'Second scene paragraph','content_html':'Second scene paragraph'},
    ]}))['ids']
    scene=assert_ok(client.post('/api/scenes',json={'title':'Bakery fight','description':'A difficult argument.','block_ids':ids}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Scene-linked task','status':'open'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'scene','target_id':str(scene['id'])}))

    assert_ok(client.post(f"/api/scenes/{scene['id']}/cut"))

    boot2=assert_ok(client.get('/api/bootstrap'))
    assert all(row['id']!=scene['id'] for row in boot2['scenes'])
    blocks=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    assert len(blocks)==1 and blocks[0]['text_content']=='' and blocks[0]['scene_id'] is None
    cuts=assert_ok(client.get('/api/cuts'))
    cut=cuts[0]
    assert cut['source_scene']=='Bakery fight'
    assert 'First scene paragraph' in cut['content_text'] and 'Second scene paragraph' in cut['content_text']
    assert assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))==[]


def test_v4187_chapter_delete_does_not_create_cut(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=next(t for t in boot['tabs'] if t['is_manuscript']); first=boot['chapters'][0]
    chapter=assert_ok(client.post('/api/chapters',json={'title':'Delete outright','tab_id':tab['id']}))
    ids=assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'text_content':'Do not preserve in Cuts','content_html':'Do not preserve in Cuts'}]}))['ids']
    scene=assert_ok(client.post('/api/scenes',json={'title':'Delete scene too','block_ids':ids}))
    before=len(assert_ok(client.get('/api/cuts')))

    assert_ok(client.delete(f"/api/chapters/{chapter['id']}/permanent"))

    boot2=assert_ok(client.get('/api/bootstrap'))
    assert all(ch['id']!=chapter['id'] for ch in boot2['chapters'])
    assert all(sc['id']!=scene['id'] for sc in boot2['scenes'])
    assert len(assert_ok(client.get('/api/cuts')))==before
    assert any(ch['id']==first['id'] for ch in boot2['chapters'])
