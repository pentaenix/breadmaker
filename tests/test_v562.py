from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path


def test_v562_release_and_canvas_order_source_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static'/'app.js')
    html=(root/'static'/'index.html').read_text()
    app=backend_source(root)
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 2)
    assert '?v=5.6.2' in html
    assert "label:'Order…',children:canvasOrderMenuItems()" in js
    assert "['compact','vertical','horizontal','circular']" not in js  # explicit named actions stay readable
    for label in ('Compact','Line vertical','Line horizontal','Circular'):
        assert f"label:'{label}'" in js
    assert "async function orderSelectedCanvasNodes(mode)" in js
    assert 'Generated arrows therefore leave' in app


def test_v562_populate_sequence_uses_side_ports_only(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Side Populate','canvas_type':'blank'}).json()
    response=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'character_arc','x':100,'y':120})
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['edges'])>=2
    for edge in data['edges']:
        assert edge['from_port'] in {'left','right'}
        assert edge['to_port'] in {'left','right'}
        assert edge['from_port'] != edge['to_port']
