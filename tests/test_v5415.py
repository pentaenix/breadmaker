from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values

from pathlib import Path
import json
from conftest import assert_ok


def test_v5415_card_shape_contract_and_legacy_migration(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Clean shapes', 'canvas_type': 'blank'}))
    card = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'free', 'shape': 'rect', 'x': 100, 'y': 120, 'title': 'Legacy card',
    }))
    figure = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'circle', 'x': 400, 'y': 120, 'w': 180, 'h': 180,
    }))

    for legacy in ('oval', 'diamond', 'rounded'):
        response = client.post(f"/api/canvases/{canvas['id']}/nodes", json={
            'node_type': 'free', 'shape': legacy, 'title': legacy,
        })
        assert response.status_code == 400
    assert client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'oval', 'w': 180, 'h': 180,
    }).status_code == 400

    # Simulate a pre-5.4.15 database + saved Structure workspace, then rerun the
    # idempotent startup migration. Legacy Card shapes flatten to Card; old Figure
    # `oval` becomes the explicit Circle figure shape.
    with storage_mutations.tx() as con:
        con.execute("UPDATE canvas_nodes SET shape='diamond' WHERE id=?", (card['id'],))
        con.execute("UPDATE canvas_nodes SET shape='oval' WHERE id=?", (figure['id'],))
        pid = con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id']
        dev = con.execute("SELECT id FROM canvases WHERE project_id=? AND canvas_type='developmental' LIMIT 1", (pid,)).fetchone()
        if not dev:
            now = storage_values.now()
            dev_id = con.execute("INSERT INTO canvases(project_id,parent_id,position,name,description,canvas_type,developmental_scope,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, None, 99, 'Legacy dev', '', 'developmental', 'chapters', now, now)).lastrowid
            vid = con.execute("INSERT INTO canvas_versions(canvas_id,position,name,workspace_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (dev_id, 0, 'Legacy', json.dumps({'schema':1,'nodes':[{'id':card['id'],'node_type':'free','shape':'oval'}]}), now, now)).lastrowid
        else:
            vid = con.execute("SELECT id FROM canvas_versions WHERE canvas_id=? ORDER BY id LIMIT 1", (dev['id'],)).fetchone()['id']
            con.execute("UPDATE canvas_versions SET workspace_json=? WHERE id=?", (json.dumps({'schema':1,'nodes':[{'id':card['id'],'node_type':'free','shape':'oval'}]}), vid))

    storage_database.init_db()
    data = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    by_id = {n['id']: n for n in data['nodes']}
    assert by_id[card['id']]['shape'] == 'rect'
    assert by_id[figure['id']]['shape'] == 'circle'
    with storage_mutations.tx() as con:
        raw = con.execute("SELECT workspace_json FROM canvas_versions WHERE id=?", (vid,)).fetchone()['workspace_json']
    workspace = json.loads(raw)
    assert workspace['nodes'][0]['shape'] == 'rect'


def test_v5415_static_canvas_cleanup_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    config = (root / 'config' / 'story_templates.json').read_text(encoding='utf-8')

    assert "addEventListener('wheel',event=>{if(!state.currentCanvas)return;event.preventDefault()" in js
    assert "$('#canvasStage').addEventListener('dblclick'" not in js
    assert 'Add oval here' not in js and 'Add diamond here' not in js
    assert "{label:'Add figure',children:" in js
    assert "const FIGURE_SHAPES=[{id:'rect',name:'Rectangle'},{id:'circle',name:'Circle'}]" in js
    assert 'LEGACY_CARD_SHAPES' not in js
    assert "node.shape==='diamond'" not in js
    assert '.canvas-node.diamond' not in css
    assert '.canvas-node.oval' not in css
    assert '.canvas-figure.circle' in css
    assert '"shape": "diamond"' not in config
    assert '"shape": "rounded"' not in config
