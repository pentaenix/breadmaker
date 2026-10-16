from __future__ import annotations

from api.canvas import developmental as canvas_developmental

import sqlite3
from pathlib import Path

from conftest import assert_ok


def _chapter_order(client, tab_id):
    boot=assert_ok(client.get('/api/bootstrap'))
    return [c['id'] for c in boot['chapters'] if c['tab_id']==tab_id and not c.get('is_implicit')]


def test_v540_versions_are_canvas_state_until_apply(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    ch3=assert_ok(client.post('/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']}))
    original=[ch1['id'],ch2['id'],ch3['id']]
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental','developmental_scope':'chapters'}))
    versions=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions']
    assert [v['name'] for v in versions]==['Version 1']
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); nodes=sorted(detail['nodes'],key=lambda n:n['x'])
    # Reorder the proposal only. Manuscript remains untouched.
    order=[nodes[2],nodes[0],nodes[1]]
    items=[]; x=120
    for n in order:
        items.append({'node_id':n['id'],'x':x,'y':118}); x+=280
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout",json={'items':items}))
    assert _chapter_order(client,tab['id'])==original
    alt=assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions",json={'name':'Alternative','from_version_id':versions[0]['id']}))
    assert _chapter_order(client,tab['id'])==original
    # Navigating named versions also changes Canvas geometry only.
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{versions[0]['id']}/activate"))
    assert _chapter_order(client,tab['id'])==original
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{alt['id']}/activate"))
    assert _chapter_order(client,tab['id'])==original
    # Apply is the sole mutation boundary.
    current=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    ordered=[n['id'] for n in sorted(current['nodes'],key=lambda n:n['x']) if n['y']<286]
    assert_ok(client.post('/api/developmental/apply',json={'ordered_node_ids':ordered}))
    assert _chapter_order(client,tab['id'])==[ch3['id'],ch1['id'],ch2['id']]


def test_v540_new_structure_is_unplaced_and_missing_history_is_retained(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental','developmental_scope':'chapters'}))
    version=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    initial=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{version['id']}"))
    assert {i['entity_id'] for i in initial['items']}=={ch1['id'],ch2['id']}
    # A Chapter created after this outline is discovered, but not guessed into it.
    ch3=assert_ok(client.post('/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']}))
    live=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); n3=next(n for n in live['nodes'] if n['chapter_id']==ch3['id'])
    assert n3['y']>=390
    # A removed historical identity remains in the named outline as a ghost item.
    assert_ok(client.delete(f"/api/chapters/{ch2['id']}"))
    saved=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{version['id']}"))
    assert any(i['entity_id']==ch2['id'] and i['title']=='Chapter 2' for i in saved['items'])
    live=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert all(n.get('chapter_id')!=ch2['id'] for n in live['nodes'])


def test_v540_version_update_preserves_missing_items_and_can_remove_them(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    v=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    assert_ok(client.delete(f"/api/chapters/{ch2['id']}"))
    updated=assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v['id']}/update"))
    ghost=next(i for i in updated['items'] if i['entity_id']==ch2['id'])
    assert_ok(client.delete(f"/api/canvases/{canvas['id']}/versions/{v['id']}/items/{ghost['id']}"))
    after=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v['id']}"))
    assert not any(i['entity_id']==ch2['id'] for i in after['items'])


def test_v540_pre_version_schema_bootstraps_version_one_and_removes_only_old_sequence_chain(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Legacy Development','canvas_type':'developmental'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); structural=sorted(detail['nodes'],key=lambda n:n['x'])
    free=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'shape':'rect','x':180,'y':430,'title':'Idea'}))
    writer_edge=assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges",json={'from_node_id':structural[0]['id'],'to_node_id':free['id'],'from_port':'bottom','to_port':'top'}))
    db=app_module_fixture.storage.DB_PATH
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        # Recreate the exact generated 5.3.3 sequence arrow, then remove the 5.4
        # marker tables so init_db sees a real upgrade boundary.
        con.execute("INSERT INTO canvas_edges(canvas_id,from_node_id,to_node_id,from_port,to_port,label,color,line_style,end_style,line_width,route_style,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",(canvas['id'],structural[0]['id'],structural[1]['id'],'right','left','','','solid','arrow',1.5,'straight'))
        con.execute('DROP TABLE canvas_version_items');con.execute('DROP TABLE canvas_versions');con.commit()
    finally: con.close()
    app_module_fixture.storage.init_db()
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    canvas_developmental.sync_developmental_canvases(pid)
    versions=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions']
    assert len(versions)==1 and versions[0]['name']=='Version 1'
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert len(detail['nodes'])==3
    assert [e['id'] for e in detail['edges']]==[writer_edge['id']]


def test_v540_new_structure_discovered_across_separate_syncs_does_not_overlap(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental'}))
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    first=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); n2=next(n for n in first['nodes'] if n.get('chapter_id')==ch2['id'])
    ch3=assert_ok(client.post('/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']}))
    second=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); n3=next(n for n in second['nodes'] if n.get('chapter_id')==ch3['id'])
    assert n2['y']>=390 and n3['y']>=390
    assert (n2['x'],n2['y']) != (n3['x'],n3['y'])


def test_v540_history_and_integrity_manage_canvas_versions(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]
    assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Versions','canvas_type':'developmental'}))
    versions=assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions']
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions",json={'name':'Alternative','from_version_id':versions[0]['id']}))
    storage=app_module_fixture.storage
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert storage.validate_project_invariants(con,pid)['ok']
        tree=storage.materialize_revision_tree(con,storage.revision_tree(con,pid))
        assert len(tree['canvas_versions'])==2
        assert tree['canvas_version_items']
        assert storage.validate_revision_tree(con,tree)['ok']
        coverage=storage.history_schema_classification(con)
        assert not coverage['unclassified'] and not coverage['missing']


def test_v540_release_metadata_and_canvas_contract_docs():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    readme=(root/'README.md').read_text(encoding='utf-8')
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert readme.startswith('# Breadmaker Editor 5.4.16')
    assert 'Structure Lane' in readme and 'named structural versions' in readme.lower()
    assert architecture.startswith('# Breadmaker Editor 5.4.16')
    assert 'Apply to Manuscript' in architecture and 'Canvas→Manuscript mutation boundary' in architecture
    assert not list(root.glob('HARDENING_*.md'))
