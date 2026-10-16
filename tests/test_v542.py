from __future__ import annotations

from pathlib import Path

from conftest import assert_ok


def _main_tab(boot):
    return next(t for t in boot['tabs'] if t.get('is_manuscript'))


def test_v542_developmental_and_project_structure_sources_use_only_main_manuscript(client, fresh_project):
    boot = fresh_project['bootstrap']
    main = _main_tab(boot)
    main_first = next(c for c in boot['chapters'] if c['tab_id'] == main['id'] and not c.get('is_implicit'))

    main_second = assert_ok(client.post('/api/chapters', json={'title': 'Main Chapter 2', 'tab_id': main['id']}))
    scratch = assert_ok(client.post('/api/documents', json={'title': 'Scratch', 'is_manuscript': False}))
    scratch_chapter = assert_ok(client.post('/api/chapters', json={'title': 'Scratch Chapter', 'tab_id': scratch['id']}))

    main_scene = assert_ok(client.post('/api/scenes', json={'title': 'Main Scene', 'tab_id': main['id']}))
    scratch_scene = assert_ok(client.post('/api/scenes', json={'title': 'Scratch Scene', 'tab_id': scratch['id']}))

    chapter_canvas = assert_ok(client.post('/api/canvases', json={
        'name': 'Main Chapters Only', 'canvas_type': 'developmental', 'developmental_scope': 'chapters'
    }))
    chapter_detail = assert_ok(client.get(f"/api/canvases/{chapter_canvas['id']}"))
    chapter_nodes = [n for n in chapter_detail['nodes'] if n['node_type'] == 'chapter']
    assert {n['chapter_id'] for n in chapter_nodes} == {main_first['id'], main_second['id']}
    assert scratch_chapter['id'] not in {n['chapter_id'] for n in chapter_nodes}

    scene_canvas = assert_ok(client.post('/api/canvases', json={
        'name': 'Main Scenes Only', 'canvas_type': 'developmental', 'developmental_scope': 'scenes'
    }))
    scene_detail = assert_ok(client.get(f"/api/canvases/{scene_canvas['id']}"))
    scene_nodes = [n for n in scene_detail['nodes'] if n['node_type'] == 'scene']
    assert {n['scene_id'] for n in scene_nodes} == {main_scene['id']}
    assert scratch_scene['id'] not in {n['scene_id'] for n in scene_nodes}

    catalog = assert_ok(client.get('/api/canvas-populate'))
    project_templates = next(c for c in catalog['categories'] if c['id'] == 'project')['templates']
    scene_template = next(t for t in project_templates if t['id'] == 'scenes')
    assert scene_template['item_count'] == 1

    normal = assert_ok(client.post('/api/canvases', json={'name': 'Populate Check', 'canvas_type': 'blank'}))
    populated = assert_ok(client.post(f"/api/canvases/{normal['id']}/populate", json={'template_id': 'scenes', 'x': 100, 'y': 100}))
    assert {n['scene_id'] for n in populated['nodes']} == {main_scene['id']}


def test_v542_structural_tile_size_is_an_api_invariant_when_moved_to_desk(client, fresh_project):
    boot = fresh_project['bootstrap']
    main = _main_tab(boot)
    assert_ok(client.post('/api/chapters', json={'title': 'Main Chapter 2', 'tab_id': main['id']}))
    canvas = assert_ok(client.post('/api/canvases', json={
        'name': 'Size Contract', 'canvas_type': 'developmental', 'developmental_scope': 'chapters'
    }))
    detail = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    node = next(n for n in detail['nodes'] if n['node_type'] == 'chapter')

    # Even if stale/legacy client data manages to alter structural dimensions,
    # crossing the Developmental layout boundary restores the canonical tile size.
    assert_ok(client.patch(f"/api/nodes/{node['id']}", json={'w': 347, 'h': 181}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/developmental-layout", json={'items': [
        {'node_id': node['id'], 'lane_position': None, 'x': 420, 'y': 330}
    ]}))
    moved = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    moved_node = next(n for n in moved['nodes'] if n['id'] == node['id'])
    assert moved_node['lane_position'] is None
    assert (int(moved_node['w']), int(moved_node['h'])) == (143, 52)


def test_v542_release_metadata_and_docs():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert readme.startswith('# Breadmaker Editor 5.4.16')
    assert 'Main Manuscript' in readme and 'Structure Order' in readme
    assert architecture.startswith('# Breadmaker Editor 5.4.16')
    assert 'Main Manuscript' in architecture and '143' in architecture and '52' in architecture
