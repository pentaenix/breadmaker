from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v556_release_contract_and_non_destructive_card_display_source():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text()
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    app = backend_source(root)
    storage = storage_source(root)

    assert tuple(map(int,(root / 'VERSION').read_text().strip().split('.'))) >= (5, 5, 6)
    assert '?v=5.5.6' in html
    assert "display_mode:str=\"full\"" in app
    assert "display_mode TEXT NOT NULL DEFAULT 'full'" in storage
    assert "function cardDisplayControl(node)" in js
    assert "await flushPendingCanvasNodeSaves();await applyNodePatch(node,{display_mode:mode}" in js
    assert "titleEl?titleEl.innerText:(node?.title||'')" in js
    assert "bodyEl?bodyEl.innerText:(node?.body||'')" in js
    assert 'card-display-options' in css
    assert 'display_mode:cardDisplayMode(node)' in js
    assert "'display_mode':n.get('display_mode')" in app
    assert "x.get('display_mode','full')" in storage


def test_v556_display_mode_api_preserves_hidden_card_text(client, fresh_project):
    canvas = client.post('/api/canvases', json={'name': 'Display modes', 'canvas_type': 'blank'}).json()
    node = client.post(
        f"/api/canvases/{canvas['id']}/nodes",
        json={
            'node_type': 'free',
            'shape': 'rect',
            'title': 'Keep this header',
            'body': 'Keep this body too',
            'display_mode': 'full',
        },
    ).json()

    response = client.patch(f"/api/nodes/{node['id']}", json={'display_mode': 'header'})
    assert response.status_code == 200, response.text
    data = client.get(f"/api/canvases/{canvas['id']}").json()
    saved = next(item for item in data['nodes'] if item['id'] == node['id'])
    assert saved['display_mode'] == 'header'
    assert saved['title'] == 'Keep this header'
    assert saved['body'] == 'Keep this body too'

    response = client.patch(f"/api/nodes/{node['id']}", json={'display_mode': 'body'})
    assert response.status_code == 200, response.text
    data = client.get(f"/api/canvases/{canvas['id']}").json()
    saved = next(item for item in data['nodes'] if item['id'] == node['id'])
    assert saved['display_mode'] == 'body'
    assert saved['title'] == 'Keep this header'
    assert saved['body'] == 'Keep this body too'

    response = client.patch(f"/api/nodes/{node['id']}", json={'display_mode': 'full'})
    assert response.status_code == 200, response.text
    data = client.get(f"/api/canvases/{canvas['id']}").json()
    saved = next(item for item in data['nodes'] if item['id'] == node['id'])
    assert saved['display_mode'] == 'full'
    assert saved['title'] == 'Keep this header'
    assert saved['body'] == 'Keep this body too'


def test_v556_rejects_display_mode_on_non_card(client, fresh_project):
    canvas = client.post('/api/canvases', json={'name': 'Display modes', 'canvas_type': 'blank'}).json()
    image = client.post(
        f"/api/canvases/{canvas['id']}/nodes",
        json={'node_type': 'image', 'shape': 'rect', 'image_path': '', 'display_mode': 'header'},
    ).json()
    assert image['display_mode'] == 'full'
    response = client.patch(f"/api/nodes/{image['id']}", json={'display_mode': 'body'})
    assert response.status_code == 400


def test_v556_developmental_versions_own_card_display_mode(client, fresh_project):
    from conftest import assert_ok

    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Development', 'canvas_type': 'developmental'}))
    v1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    card = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'free', 'shape': 'rect', 'x': 300, 'y': 430,
        'title': 'Versioned Card', 'body': 'Both pieces survive', 'display_mode': 'header',
    }))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/update", json={}))

    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={'name': 'Version 2', 'from_version_id': v1['id']}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    assert_ok(client.patch(f"/api/nodes/{card['id']}", json={'display_mode': 'body'}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/update", json={}))

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    one = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    c1 = next(n for n in one['nodes'] if n['id'] == card['id'])
    assert (c1['display_mode'], c1['title'], c1['body']) == ('header', 'Versioned Card', 'Both pieces survive')

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    two = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    c2 = next(n for n in two['nodes'] if n['id'] == card['id'])
    assert (c2['display_mode'], c2['title'], c2['body']) == ('body', 'Versioned Card', 'Both pieces survive')
