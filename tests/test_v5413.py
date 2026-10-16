from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_v5413_figures_are_background_objects_and_circles_stay_circular(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Figures', 'canvas_type': 'blank'}))
    rect = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'rect', 'x': 80, 'y': 90, 'w': 300, 'h': 180,
    }))
    circle = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'circle', 'x': 420, 'y': 90, 'w': 180, 'h': 180,
    }))
    card = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'free', 'shape': 'rect', 'x': 120, 'y': 120, 'w': 220, 'h': 125, 'title': 'Idea',
    }))
    assert rect['title'] == '' and rect['body'] == '' and rect['color'] == ''
    assert circle['w'] == circle['h'] == 180

    invalid = client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'diamond', 'w': 100, 'h': 100,
    })
    assert invalid.status_code == 400
    ellipse = client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'circle', 'w': 180, 'h': 120,
    })
    assert ellipse.status_code == 400
    content = client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'rect', 'w': 180, 'h': 120, 'title': 'Not allowed',
    })
    assert content.status_code == 400

    # Figures are deliberately outside the connector graph.
    edge = client.post(f"/api/canvases/{canvas['id']}/edges", json={
        'from_node_id': rect['id'], 'to_node_id': card['id'], 'from_port': 'right', 'to_port': 'left',
    })
    assert edge.status_code == 400

    # Geometry is editable, but a circle cannot be stretched into an ellipse.
    assert_ok(client.patch(f"/api/nodes/{rect['id']}", json={'x': 100, 'y': 110, 'w': 340, 'h': 200}))
    bad_resize = client.patch(f"/api/nodes/{circle['id']}", json={'w': 220, 'h': 180})
    assert bad_resize.status_code == 400


def test_v5413_ui_static_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))

    assert 'id="addFigureBtn"' in html
    assert 'id="canvasFigurePalette"' in html
    assert 'id="figures" class="canvas-figures"' in html
    assert "const SHAPES=[{id:'rect',symbol:'▭',name:'Card'},{id:'text',symbol:'T',name:'Text only'}]" in js
    assert "const FIGURE_SHAPES=[{id:'rect',name:'Rectangle'},{id:'circle',name:'Circle'}]" in js
    assert 'function beginCanvasFigureDraw(event,shape)' in js
    assert "node.node_type==='figure'" in js
    assert '.canvas-figure{' in css
    assert 'background:transparent!important' in css
    assert 'border:1px solid #000!important' in css

    lasso = js.split('function beginCanvasLasso(event){', 1)[1].split("$('#canvasStage').addEventListener", 1)[0]
    assert 'n.x>=left&&n.x+size.w<=right&&n.y>=top&&n.y+size.h<=bottom' in lasso
    assert 'n.x<=right&&n.x+size.w>=left' not in lasso
    assert "!['routing','label','figure'].includes(node.node_type)" in js


def test_v5413_figures_participate_in_developmental_workspace_versions(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Figure Versions', 'canvas_type': 'developmental'}))
    v1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    figure = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'rect', 'x': 260, 'y': 380, 'w': 360, 'h': 220,
    }))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/update", json={}))

    assert_ok(client.patch(f"/api/nodes/{figure['id']}", json={'x': 920, 'y': 640, 'w': 420, 'h': 260}))
    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={
        'name': 'Figure Layout 2', 'from_version_id': v1['id'],
    }))

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    node1 = next(n for n in assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes'] if n['id'] == figure['id'])
    assert (node1['x'], node1['y'], node1['w'], node1['h']) == (260.0, 380.0, 360.0, 220.0)

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    node2 = next(n for n in assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes'] if n['id'] == figure['id'])
    assert (node2['x'], node2['y'], node2['w'], node2['h']) == (920.0, 640.0, 420.0, 260.0)
