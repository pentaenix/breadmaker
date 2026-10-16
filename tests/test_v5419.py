from source_contracts import frontend_source_from_app_path
from storage import mutations as storage_mutations

from pathlib import Path

from conftest import assert_ok


def test_v5419_developmental_versions_own_cards_images_and_connectors(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Development', 'canvas_type': 'developmental'}))
    v1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    asset1 = assert_ok(client.post('/api/assets', files={'file': ('one.png', b'one-image', 'image/png')}))['path']
    asset2 = assert_ok(client.post('/api/assets', files={'file': ('two.png', b'two-image', 'image/png')}))['path']
    card = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'free', 'shape': 'rect', 'x': 240, 'y': 420, 'w': 260, 'h': 150,
        'title': 'Version one card', 'body': 'First body', 'color': '#e8e8e8'
    }))
    image = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type': 'image', 'shape': 'rect', 'x': 620, 'y': 430, 'w': 320, 'h': 230, 'image_path': asset1
    }))
    edge = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={
        'from_node_id': card['id'], 'to_node_id': image['id'], 'from_port': 'right', 'to_port': 'left',
        'label': 'V1', 'line_style': 'solid', 'route_style': 'curved'
    }))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/update", json={}))

    assert_ok(client.patch(f"/api/nodes/{card['id']}", json={'x': 900, 'y': 700, 'title': 'Version two card', 'body': 'Second body', 'color': '#f2e9b8'}))
    assert_ok(client.patch(f"/api/nodes/{image['id']}", json={'x': 1180, 'y': 680, 'image_path': asset2, 'w': 410, 'h': 270}))
    assert_ok(client.patch(f"/api/edges/{edge['id']}", json={'label': 'V2', 'line_style': 'dashed', 'route_style': 'straight', 'line_width': 3}))
    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={'name': 'Version 2', 'from_version_id': v1['id']}))

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    data1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    c1 = next(n for n in data1['nodes'] if n['id'] == card['id'])
    i1 = next(n for n in data1['nodes'] if n['id'] == image['id'])
    e1 = next(e for e in data1['edges'] if e['id'] == edge['id'])
    assert (c1['title'], c1['body'], c1['x'], c1['color']) == ('Version one card', 'First body', 240.0, '#e8e8e8')
    assert (i1['image_path'], i1['x'], i1['w']) == (asset1, 620.0, 320.0)
    assert (e1['label'], e1['line_style'], e1['route_style'], e1['line_width']) == ('V1', 'solid', 'curved', 1.5)

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    data2 = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    c2 = next(n for n in data2['nodes'] if n['id'] == card['id'])
    i2 = next(n for n in data2['nodes'] if n['id'] == image['id'])
    e2 = next(e for e in data2['edges'] if e['id'] == edge['id'])
    assert (c2['title'], c2['body'], c2['x'], c2['color']) == ('Version two card', 'Second body', 900.0, '#f2e9b8')
    assert (i2['image_path'], i2['x'], i2['w']) == (asset2, 1180.0, 410.0)
    assert (e2['label'], e2['line_style'], e2['route_style'], e2['line_width']) == ('V2', 'dashed', 'straight', 3.0)

    # Removing the image from Version 2 is local to Version 2. Version 1 can still
    # restore its own image asset and connector.
    assert_ok(client.delete(f"/api/nodes/{image['id']}"))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/update", json={}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    restored = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert next(n for n in restored['nodes'] if n['id'] == image['id'])['developmental_hidden'] == 0
    assert next(n for n in restored['nodes'] if n['id'] == image['id'])['image_path'] == asset1
    assert next(e for e in restored['edges'] if e['id'] == edge['id'])['developmental_hidden'] == 0

    d1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v1['id']}"))
    d2 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v2['id']}"))
    assert d1['workspace']['schema'] == 2 and d2['workspace']['schema'] == 2
    assert image['id'] in {n['id'] for n in d1['workspace']['nodes']}
    assert image['id'] not in {n['id'] for n in d2['workspace']['nodes']}
    assert edge['id'] in {e['id'] for e in d1['workspace']['edges']}
    assert edge['id'] not in {e['id'] for e in d2['workspace']['edges']}


def test_v5419_release_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    html = (root / 'static' / 'index.html').read_text()
    assert tuple(map(int,(root / 'VERSION').read_text().strip().split('.'))) >= (5, 4, 20)
    assert '?v=5.4.20' in html
    assert "workspace.schema||0)>=2" in js
    assert 'complete Developmental workspace below it' in js


def test_v5420_empty_saved_version_does_not_inherit_newer_card(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Development', 'canvas_type': 'developmental'}))
    v1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions'][0]
    # Reproduce an older saved Version whose lower-workspace payload was empty.
    with storage_mutations.tx() as con:
        con.execute("UPDATE canvas_versions SET workspace_json='{}' WHERE id=?", (v1['id'],))
    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={'name': 'Version 2', 'from_version_id': v1['id']}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    card = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type':'free','shape':'rect','x':300,'y':430,'w':240,'h':140,'title':'Only in V2'
    }))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/update", json={}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    data = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    restored = next(n for n in data['nodes'] if n['id'] == card['id'])
    assert restored['developmental_hidden'] == 1
