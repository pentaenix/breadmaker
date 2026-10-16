from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v573_release_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 7, 3)
    html=(root/'static'/'index.html').read_text()
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    assert '?v=5.7.3' in html
    assert 'beginCanvasNestHover' in js
    assert 'moveCanvasToParent' in js
    assert 'canvasOutdentDrop' in js
    assert '.canvas-item.nest-ready' in css
    assert '.canvas-outdent-drop' in css


def test_v573_reparenting_can_preserve_subtrees(client,fresh_project):
    a=client.post('/api/canvases',json={'name':'A','canvas_type':'blank'}).json()
    b=client.post('/api/canvases',json={'name':'B','canvas_type':'blank'}).json()
    child=client.post('/api/canvases',json={'name':'B child','canvas_type':'blank','parent_id':b['id']}).json()
    moved=client.patch(f'/api/canvases/{b["id"]}',json={'parent_id':a['id']})
    assert moved.status_code==200
    boot=client.get('/api/bootstrap').json()
    assert next(c for c in boot['canvases'] if c['id']==b['id'])['parent_id']==a['id']
    assert next(c for c in boot['canvases'] if c['id']==child['id'])['parent_id']==b['id']
    # API cycle prevention remains authoritative.
    cycle=client.patch(f'/api/canvases/{a["id"]}',json={'parent_id':child['id']})
    assert cycle.status_code==400
