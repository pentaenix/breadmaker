from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json


def test_v564_release_source_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    html=(root/'static'/'index.html').read_text()
    app=backend_source(root)
    config=json.loads((root/'config'/'story_templates.json').read_text())
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 4)
    assert '?v=5.6.4' in html
    assert 'selectedEdgeIds: new Set()' in js
    assert 'canvasEdgeFullyInsideRect' in js
    assert "openCanvasPanel('Selected Connectors'" in js
    assert '.edge-line.selected{stroke:var(--accent)' not in css
    assert '.edge-line{fill:none;stroke:var(--muted)' not in css
    assert 'connect.get("route_style","straight")' in app
    sequence=[]
    for category in config['categories']:
        for template in category['templates']:
            connect=template.get('connect') or {}
            if connect.get('mode')=='sequence':
                sequence.append(template['id'])
                assert connect.get('route_style')=='straight', template['id']
    assert sequence


def test_v564_populate_connectors_are_straight_and_right_left(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Straight Populate','canvas_type':'blank'}).json()
    response=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'character_arc','x':120,'y':100})
    assert response.status_code==200,response.text
    edges=response.json()['edges']
    assert edges
    assert all(edge['route_style']=='straight' for edge in edges)
    assert all(edge['from_port']=='right' and edge['to_port']=='left' for edge in edges)
