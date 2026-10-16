from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v5418_image_ui_and_pointer_math_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    version = (root / 'VERSION').read_text().strip()

    assert tuple(map(int, version.split('.'))) >= (5, 4, 18)
    assert 'function canvasWorldMetrics()' in js
    assert 'function canvasDeltaFromClient(' in js
    assert "rect=world.getBoundingClientRect()" in js
    assert "const delta=canvasDeltaFromClient(sx,sy,e.clientX,e.clientY)" in js
    assert "label:'Replace Image…'" in js
    assert "label:'Delete Image'" in js
    assert "selectOnlyNode(node.id,{inspect:false})" in js
    assert "selected.every(n=>['figure','image'].includes(n.node_type))" in js
    assert 'Images are free canvas objects.' not in js
    assert 'Resize on canvas' not in js
    assert '.image-size-readout' not in css


def test_v5418_canvas_image_can_replace_project_asset(client, fresh_project):
    canvas = client.post('/api/canvases', json={'name': 'Images', 'canvas_type': 'blank'}).json()
    first_asset = client.post('/api/assets', files={'file': ('first.png', b'first-bytes', 'image/png')})
    second_asset = client.post('/api/assets', files={'file': ('second.png', b'second-bytes', 'image/png')})
    assert first_asset.status_code == 200
    assert second_asset.status_code == 200
    first_path = first_asset.json()['path']
    second_path = second_asset.json()['path']

    created = client.post(
        f"/api/canvases/{canvas['id']}/nodes",
        json={'node_type': 'image', 'shape': 'rect', 'image_path': first_path, 'x': 140, 'y': 160, 'w': 320, 'h': 230},
    )
    assert created.status_code == 200, created.text
    node = created.json()

    replaced = client.patch(f"/api/nodes/{node['id']}", json={'image_path': second_path})
    assert replaced.status_code == 200, replaced.text
    detail = client.get(f"/api/canvases/{canvas['id']}").json()
    saved = next(n for n in detail['nodes'] if n['id'] == node['id'])
    assert saved['image_path'] == second_path
    assert saved['x'] == 140
    assert saved['y'] == 160
    assert saved['w'] == 320
    assert saved['h'] == 230

    card = client.post(
        f"/api/canvases/{canvas['id']}/nodes",
        json={'node_type': 'free', 'shape': 'rect', 'title': 'Card'},
    ).json()
    invalid = client.patch(f"/api/nodes/{card['id']}", json={'image_path': second_path})
    assert invalid.status_code == 400
