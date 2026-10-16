from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from fastapi.testclient import TestClient

import app


def test_v572_release_and_canvas_navigator_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 7, 2)
    html=(root/'static'/'index.html').read_text()
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    assert '?v=5.7.2' in html
    assert "collapsed?'▸':'▾'" in js
    assert 'canvas-grip chapter-grip' in js
    assert "api('/api/canvases/reorder'" in js
    assert '.canvas-item.drop-before' in css and '.canvas-item.drop-after' in css



def test_v572_canvas_reorder_only_reorders_siblings(client,fresh_project):
    a=client.post('/api/canvases',json={'name':'A','canvas_type':'blank'}).json()
    b=client.post('/api/canvases',json={'name':'B','canvas_type':'blank'}).json()
    child=client.post('/api/canvases',json={'name':'Child','canvas_type':'blank','parent_id':a['id']}).json()
    r=client.post('/api/canvases/reorder',json={'ids':[b['id'],a['id']]})
    assert r.status_code==200
    boot=client.get('/api/bootstrap').json()
    roots=[c for c in boot['canvases'] if c['parent_id'] is None]
    assert [c['id'] for c in roots]==[b['id'],a['id']]
    assert next(c for c in boot['canvases'] if c['id']==child['id'])['parent_id']==a['id']
    bad=client.post('/api/canvases/reorder',json={'ids':[a['id'],child['id']]})
    assert bad.status_code==400
