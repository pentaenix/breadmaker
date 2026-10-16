from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_v5411_timeline_is_retired_and_existing_timeline_migrates_to_blank(client, fresh_project, app_module_fixture):
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Old Timeline', 'canvas_type': 'blank'}))
    with app_module_fixture.storage.tx() as con:
        con.execute("UPDATE canvases SET canvas_type='timeline' WHERE id=?", (canvas['id'],))
    app_module_fixture.storage.init_db()
    with app_module_fixture.storage.tx() as con:
        row = con.execute('SELECT canvas_type FROM canvases WHERE id=?', (canvas['id'],)).fetchone()
    assert row['canvas_type'] == 'blank'
    response = client.post('/api/canvases', json={'name': 'No New Timeline', 'canvas_type': 'timeline'})
    assert response.status_code == 400


def test_v5411_card_palette_and_automatic_contrast_static_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert 'id="canvasCardPalette"' in html
    assert 'function beginCanvasCardDraw(event,shape)' in js
    assert 'data-canvas-card-shape' in js
    assert 'openAddNodePanel' not in js
    assert "['timeline'" not in js
    assert 'canvas-timeline-axis' not in css
    assert 'function automaticCanvasTextColor(node)' in js
    assert "node.node_type==='free')?automaticCanvasTextColor(node)" in js
    regular = js.split("const textOnly=node.shape==='text'", 1)[1].split('function scheduleSplitLayout', 1)[0]
    assert '<label>Text color</label>' not in regular
