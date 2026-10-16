from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import tempfile

from conftest import assert_ok


def _real_chapters(client, tab_id):
    boot=assert_ok(client.get('/api/bootstrap'))
    return [c for c in boot['chapters'] if c['tab_id']==tab_id and not c.get('is_implicit')]


def test_v541_lane_positions_scale_without_world_geometry(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    # 300 items is deliberately larger than any screen-sized lane. The lane order,
    # not a growing x coordinate, must be the structural source of truth.
    for i in range(2,301):
        assert_ok(client.post('/api/chapters',json={'title':f'Chapter {i}','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Large Development','canvas_type':'developmental','developmental_scope':'chapters'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    nodes=sorted([n for n in detail['nodes'] if n['node_type']=='chapter'],key=lambda n:n['lane_position'])
    assert len(nodes)==300
    assert [n['lane_position'] for n in nodes]==list(range(300))
    assert max(float(n['x']) for n in nodes)<=120
    assert all(int(n['w'])==143 and int(n['h'])==52 for n in nodes)


def test_v541_planning_cards_can_share_lane_but_apply_ignores_them(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted(detail['nodes'],key=lambda n:n['lane_position'])
    idea=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':400,'y':500,'title':'Future Chapter'}))
    items=[
        {'node_id':structural[0]['id'],'lane_position':0},
        {'node_id':idea['id'],'lane_position':1},
        {'node_id':structural[1]['id'],'lane_position':2},
    ]
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':items}))
    current=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    idea_now=next(n for n in current['nodes'] if n['id']==idea['id'])
    assert idea_now['node_type']=='free' and idea_now['lane_position']==1
    structural_order=[n['id'] for n in sorted((n for n in current['nodes'] if n['node_type']=='chapter'),key=lambda n:n['lane_position'])]
    preview=assert_ok(client.post('/api/developmental/preflight',json={'ordered_node_ids':structural_order}))
    assert preview['can_apply'] is True
    assert preview['chapters']==2
    assert [c['id'] for c in _real_chapters(client,tab['id'])]==[ch1['id'],ch2['id']]


def test_v541_promote_planning_card_creates_real_chapter_and_blank_paragraph(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted(detail['nodes'],key=lambda n:n['lane_position'])
    idea=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':360,'y':450,'title':'The Ambush','body':'Maybe this belongs here'}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[
        {'node_id':structural[0]['id'],'lane_position':0},
        {'node_id':idea['id'],'lane_position':1},
        {'node_id':structural[1]['id'],'lane_position':2},
    ]}))
    promoted=assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-promote/{idea['id']}",json={}))
    assert promoted['id']==idea['id']
    assert promoted['node_type']=='chapter'
    assert promoted['chapter_id'] not in {ch1['id'],ch2['id']}
    assert promoted['lane_position']==1
    chapters=_real_chapters(client,tab['id'])
    assert [c['id'] for c in chapters]==[ch1['id'],promoted['chapter_id'],ch2['id']]
    assert next(c for c in chapters if c['id']==promoted['chapter_id'])['title']=='The Ambush'
    with app_module_fixture.storage.tx() as con:
        blocks=con.execute("SELECT block_type,content_html,text_content FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(promoted['chapter_id'],)).fetchall()
    assert len(blocks)==1
    assert tuple(blocks[0])==('paragraph','','')


def test_v541_versions_store_lane_order_not_planning_prose(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted(detail['nodes'],key=lambda n:n['lane_position'])
    idea=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':400,'y':500,'title':'Maybe flashback','body':'Planning prose'}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[
        {'node_id':structural[1]['id'],'lane_position':0},
        {'node_id':idea['id'],'lane_position':1},
        {'node_id':structural[0]['id'],'lane_position':2},
    ]}))
    version=assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions",json={'name':'With an idea'}))
    saved=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{version['id']}"))
    placed=sorted([i for i in saved['items'] if i['placed']],key=lambda i:i['lane_position'])
    assert len(placed)==2
    assert all(i['entity_type']=='chapter' for i in placed)
    assert idea['id'] not in {i['entity_id'] for i in placed}
    assert [i['entity_id'] for i in placed]==[ch2['id'],boot['chapters'][0]['id']]




def test_v541_partial_layout_cannot_collide_with_an_omitted_lane_item(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted((n for n in detail['nodes'] if n['node_type']=='chapter'),key=lambda n:n['lane_position'])
    before={n['id']:n['lane_position'] for n in structural}
    # Submit only one tile and try to move it into the other tile's occupied slot.
    # A stale/partial client must fail atomically rather than corrupting the lane.
    response=client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[{'node_id':structural[0]['id'],'lane_position':structural[1]['lane_position']}]})
    assert response.status_code==400
    after=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes']
    assert {n['id']:n['lane_position'] for n in after if n['node_type']=='chapter'}==before


def test_v541_version_activation_reserves_missing_ghost_lane_slot_for_live_planning_cards(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    versions=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions']; saved=versions[0]
    original=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted((n for n in original['nodes'] if n['node_type']=='chapter'),key=lambda n:n['lane_position'])
    assert [n['lane_position'] for n in structural]==[0,1,2]
    # Remove the middle Chapter. Version 1 must retain it as a placed missing ghost at slot 1.
    assert_ok(client.delete(f"/api/chapters/{ch2['id']}/permanent"))
    current=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); live=sorted((n for n in current['nodes'] if n['node_type']=='chapter'),key=lambda n:n['lane_position'] if n['lane_position'] is not None else 999)
    idea=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':400,'y':500,'title':'Still thinking'}))
    # Put the planning Card in numeric slot 1 before restoring the old version.
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[
        {'node_id':live[0]['id'],'lane_position':0},
        {'node_id':idea['id'],'lane_position':1},
        {'node_id':live[1]['id'],'lane_position':2},
    ]}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{saved['id']}/activate",json={}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); positions={n['id']:n['lane_position'] for n in detail['nodes'] if n.get('lane_position') is not None}
    saved_detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{saved['id']}"))
    ghost=next(i for i in saved_detail['items'] if i['entity_id']==ch2['id'])
    assert ghost['placed']==1 and ghost['lane_position']==1
    idea_row=next(n for n in detail['nodes'] if n['id']==idea['id'])
    assert idea_row['developmental_hidden']==1 and idea_row['lane_position'] is None
    assert ghost['lane_position'] not in {pos for nid,pos in positions.items() if nid!=idea['id']}
    assert len(positions.values())==len(set(positions.values()))


def _set_text_block(client,chapter_id,text):
    block=assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks'][0]
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':block.get('scene_id')}]}))
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks'][0]


def test_v541_scene_planning_promotion_infers_same_chapter_and_requires_choice_at_boundary(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    # Build two Scenes in Chapter 1 and one Scene in Chapter 2 so both inference and
    # the cross-Chapter ambiguity contract are exercised.
    first=_set_text_block(client,ch1['id'],'First scene')
    s1=assert_ok(client.post('/api/scenes',json={'title':'First','block_ids':[first['id']]}))
    s2=assert_ok(client.post('/api/scenes',json={'title':'Second','after_scene_id':s1['id']}))
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    third=_set_text_block(client,ch2['id'],'Third scene')
    s3=assert_ok(client.post('/api/scenes',json={'title':'Third','block_ids':[third['id']]}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Scene Development','canvas_type':'developmental','developmental_scope':'scenes'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); by_scene={n['scene_id']:n for n in detail['nodes'] if n['node_type']=='scene'}

    same=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':300,'y':500,'title':'Between same Chapter'}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[
        {'node_id':by_scene[s1['id']]['id'],'lane_position':0},
        {'node_id':same['id'],'lane_position':1},
        {'node_id':by_scene[s2['id']]['id'],'lane_position':2},
        {'node_id':by_scene[s3['id']]['id'],'lane_position':3},
    ]}))
    inferred=assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-promote/{same['id']}",json={}))
    assert inferred['node_type']=='scene' and inferred['chapter_id']==ch1['id']
    with app_module_fixture.storage.tx() as con:
        blank=con.execute('SELECT block_type,text_content FROM manuscript_blocks WHERE scene_id=?',(inferred['scene_id'],)).fetchall()
    assert len(blank)==1 and tuple(blank[0])==('paragraph','')

    # Resync leaves the promoted Scene in the structural lane. Put another Planning
    # Card exactly between Chapter 1's last Scene and Chapter 2's Scene.
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted((n for n in detail['nodes'] if n['node_type']=='scene'),key=lambda n:n['lane_position'])
    last_ch1=max((n for n in structural if n['chapter_id']==ch1['id']),key=lambda n:n['lane_position'])
    first_ch2=min((n for n in structural if n['chapter_id']==ch2['id']),key=lambda n:n['lane_position'])
    boundary=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':360,'y':520,'title':'Boundary Scene'}))
    # Rebuild the full lane with boundary immediately between those two neighbors.
    ordered=[]
    for n in structural:
        ordered.append(n)
        if n['id']==last_ch1['id']: ordered.append(boundary)
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':[{'node_id':n['id'],'lane_position':i} for i,n in enumerate(ordered)]}))
    ambiguous=client.post(f"/api/canvases/{canvas['id']}/developmental-promote/{boundary['id']}",json={})
    assert ambiguous.status_code==409
    chosen=assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-promote/{boundary['id']}",json={'chapter_id':ch2['id']}))
    assert chosen['node_type']=='scene' and chosen['chapter_id']==ch2['id']

def test_v541_migrates_real_540_geometry_and_resets_only_old_developmental_zoom(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Legacy 5.4 Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); nodes=sorted([n for n in detail['nodes'] if n['node_type']=='chapter'],key=lambda n:n['lane_position'])
    storage=app_module_fixture.storage
    # Copy the live fixture DB into an isolated file, then make its two tables look
    # exactly like 5.4: world x/y order exists, lane_position does not.
    old_path=storage.DB_PATH
    tmp=Path(tempfile.mkdtemp(prefix='breadmaker-v541-migration-'))/'breadmaker.db'
    src=sqlite3.connect(old_path); dst=sqlite3.connect(tmp)
    try: src.backup(dst)
    finally: src.close(); dst.close()
    con=sqlite3.connect(tmp)
    try:
        # Deliberately make the visual geometry nontrivial so migration must sort x.
        for x,node in zip((760,120,430),nodes): con.execute('UPDATE canvas_nodes SET x=?,y=118 WHERE id=?',(x,node['id']))
        con.execute("UPDATE canvas_version_items SET x=(CASE entity_id WHEN ? THEN 760 WHEN ? THEN 120 ELSE 430 END),y=118,placed=1 WHERE version_id=(SELECT id FROM canvas_versions WHERE canvas_id=? ORDER BY id LIMIT 1)",(nodes[0]['chapter_id'],nodes[1]['chapter_id'],canvas['id']))
        pid=con.execute('SELECT id FROM projects ORDER BY id DESC LIMIT 1').fetchone()[0]
        con.execute("INSERT INTO ui_state(project_id,key,value_json,updated_at) VALUES(?,?,?,datetime('now')) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json",(pid,f"canvas-viewport-{canvas['id']}",json.dumps({'zoom':0.22,'left':800,'top':91,'mode':'pointer','textSize':15})))
        con.execute('ALTER TABLE canvas_nodes DROP COLUMN lane_position')
        con.execute('ALTER TABLE canvas_version_items DROP COLUMN lane_position')
        con.commit()
    finally: con.close()
    try:
        storage.DB_PATH=tmp; storage.init_db()
        con=sqlite3.connect(tmp); con.row_factory=sqlite3.Row
        try:
            migrated=[dict(r) for r in con.execute("SELECT chapter_id,x,y,lane_position,w,h FROM canvas_nodes WHERE canvas_id=? AND node_type='chapter' ORDER BY lane_position",(canvas['id'],))]
            saved=[dict(r) for r in con.execute("SELECT entity_id,lane_position,placed FROM canvas_version_items WHERE version_id=(SELECT id FROM canvas_versions WHERE canvas_id=? ORDER BY id LIMIT 1) ORDER BY lane_position",(canvas['id'],))]
            viewport=json.loads(con.execute('SELECT value_json FROM ui_state WHERE project_id=? AND key=?',(pid,f"canvas-viewport-{canvas['id']}")).fetchone()[0])
        finally: con.close()
    finally: storage.DB_PATH=old_path
    assert [r['x'] for r in migrated]==[120,430,760]
    assert [r['lane_position'] for r in migrated]==[0,1,2]
    assert all((r['w'],r['h'])==(143,52) for r in migrated)
    assert [r['lane_position'] for r in saved]==[0,1,2]
    assert viewport['zoom']==1
    assert viewport['left']==800 and viewport['top']==91 and viewport['mode']=='pointer' and viewport['textSize']==15

def test_v541_release_metadata_and_docs():
    root=Path(__file__).resolve().parents[1]
    # This test intentionally stays red until the final release bump, so a partially
    # completed implementation cannot be packaged as 5.4.1 by accident.
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    readme=(root/'README.md').read_text(encoding='utf-8')
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert readme.startswith('# Breadmaker Editor 5.4.16')
    assert 'Planning Card' in readme and 'horizontal' in readme.lower()
    assert architecture.startswith('# Breadmaker Editor 5.4.16')
    assert 'lane_position' in architecture and 'Canvas→Manuscript mutation boundary' in architecture
