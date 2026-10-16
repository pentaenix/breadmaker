from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import database as storage_database

from pathlib import Path
from conftest import assert_ok


def test_v547_structure_panel_static_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert html.index('id="addImage"') < html.index('id="canvasStructureBtn"')
    assert 'Structure Order <em id="developmentalStructureScope">(Chapter)</em>' in html
    assert '<b>Show Structure</b>' in js
    assert 'role="switch"' in js and 'aria-checked=' in js
    assert 'class="bare-button structure-add-version"' in js
    assert '>+</button>' in js
    assert 'structure-help-button' not in css
    assert 'structure-help-tooltip' not in css
    assert '--developmental-tile-width:143px' in css
    assert 'developmental-tile-kind' not in js[js.index('function renderDevelopmentalLane'):js.index('function renderCanvasGuides')]


def test_v547_existing_structural_tiles_normalize_to_compact_width(client, fresh_project):
    tab = fresh_project['bootstrap']['tabs'][0]
    assert_ok(client.post('/api/chapters', json={'title': 'Chapter 2', 'tab_id': tab['id']}))
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Development', 'canvas_type': 'developmental'}))
    data = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    structural = [n for n in data['nodes'] if n['node_type'] == 'chapter' and n['chapter_id']]
    assert structural and all((int(n['w']), int(n['h'])) == (143, 52) for n in structural)

    # Simulate a 5.4.6 database that still has the old 220px structural width.
    with storage_database.connect() as con:
        con.execute("UPDATE canvas_nodes SET w=220,h=52 WHERE id=?", (structural[0]['id'],))
        con.commit()
    storage_database.init_db()
    with storage_database.connect() as con:
        row = con.execute("SELECT w,h FROM canvas_nodes WHERE id=?", (structural[0]['id'],)).fetchone()
    assert (int(row['w']), int(row['h'])) == (143, 52)
