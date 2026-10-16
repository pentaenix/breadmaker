from __future__ import annotations

from conftest import assert_ok


def _blocks(client,cid):
    return assert_ok(client.get(f'/api/chapters/{cid}'))['blocks']


def _text(client,cid,text):
    block=_blocks(client,cid)[0]
    assert_ok(client.put(f'/api/chapters/{cid}/blocks',json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':block.get('scene_id')}]}))
    return _blocks(client,cid)[0]


def test_v533_developmental_scope_cards_lane_sequence_and_apply(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))

    # Scene mode is visible in the UI, but the server also refuses to create a fake
    # Scene plan when there is no written Scene to mirror.
    rejected=client.post('/api/canvases',json={'name':'Scenes','canvas_type':'developmental','developmental_scope':'scenes'})
    assert rejected.status_code==400

    chapters=assert_ok(client.post('/api/canvases',json={'name':'Chapters','canvas_type':'developmental','developmental_scope':'chapters'}))
    detail=assert_ok(client.get(f"/api/canvases/{chapters['id']}"))
    assert detail['canvas']['developmental_scope']=='chapters'
    assert {n['node_type'] for n in detail['nodes']}=={'chapter'}
    ordered=sorted(detail['nodes'],key=lambda n:n['x'])
    assert len({n['y'] for n in ordered})==1
    # 5.4 supersedes the 5.3.3 generated arrow chain: the Structure Lane itself
    # is the sequence, leaving connectors entirely writer-owned.
    assert detail['edges']==[]

    first=_text(client,ch1['id'],'First scene'); second=_text(client,ch2['id'],'Second scene')
    s1=assert_ok(client.post('/api/scenes',json={'title':'First','block_ids':[first['id']]}))
    s2=assert_ok(client.post('/api/scenes',json={'title':'Second','block_ids':[second['id']]}))
    scenes=assert_ok(client.post('/api/canvases',json={'name':'Scenes','canvas_type':'developmental','developmental_scope':'scenes'}))
    detail=assert_ok(client.get(f"/api/canvases/{scenes['id']}"))
    assert detail['canvas']['developmental_scope']=='scenes'
    assert {n['node_type'] for n in detail['nodes']}=={'scene'}
    ordered=sorted(detail['nodes'],key=lambda n:n['x'])
    assert [n['scene_id'] for n in ordered]==[s1['id'],s2['id']]
    assert len({n['y'] for n in ordered})==1
    assert detail['edges']==[]

    assert_ok(client.post('/api/developmental/apply',json={'ordered_node_ids':[ordered[1]['id'],ordered[0]['id']]}))
    assert any(b['scene_id']==s2['id'] for b in _blocks(client,ch1['id']))
    assert any(b['scene_id']==s1['id'] for b in _blocks(client,ch2['id']))


def test_v533_pre_scope_canvas_schema_migrates_to_chapters(client,fresh_project,app_module_fixture):
    import sqlite3
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Legacy Development','canvas_type':'developmental','developmental_scope':'chapters'}))
    db=app_module_fixture.storage.DB_PATH
    con=sqlite3.connect(db)
    try:
        con.execute('ALTER TABLE canvases DROP COLUMN developmental_scope');con.commit()
    finally: con.close()
    app_module_fixture.storage.init_db()
    con=sqlite3.connect(db);con.row_factory=sqlite3.Row
    try:
        cols={row['name'] for row in con.execute('PRAGMA table_info(canvases)')}
        row=con.execute('SELECT developmental_scope FROM canvases WHERE id=?',(canvas['id'],)).fetchone()
        assert 'developmental_scope' in cols and row['developmental_scope']=='chapters'
    finally: con.close()
