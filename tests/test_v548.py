from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import database as storage_database

from pathlib import Path
from conftest import assert_ok


def test_v548_manuscript_ui_reference_and_structure_static_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert '--developmental-tile-width:143px' in css
    assert 'structure-help-button' not in css
    assert 'structure-help-tooltip' not in css
    assert 'STRUCTURE_HELP_ICON' not in js
    assert 'data-structure-help' not in js
    assert 'data-structure-description' in js
    assert 'structurePanelHoverTimer=setTimeout' in js and '},300);' in js
    assert "positionMarkerPanelHover(card,target)" in js
    assert 'id="structurePanelHoverCard" class="marker-panel-hover-card' in html
    assert 'Manuscript UI reference contract' in architecture
    assert 'do not add `i`/help icons' in architecture


def test_v548_existing_structural_tiles_normalize_from_v547_width(client, fresh_project):
    tab = fresh_project['bootstrap']['tabs'][0]
    assert_ok(client.post('/api/chapters', json={'title': 'Chapter 2', 'tab_id': tab['id']}))
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Development', 'canvas_type': 'developmental'}))
    data = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    structural = [n for n in data['nodes'] if n['node_type'] == 'chapter' and n['chapter_id']]
    assert structural and all((int(n['w']), int(n['h'])) == (143, 52) for n in structural)

    # Simulate a 5.4.7 database using the former 190px structural width.
    with storage_database.connect() as con:
        con.execute("UPDATE canvas_nodes SET w=190,h=52 WHERE id=?", (structural[0]['id'],))
        con.commit()
    storage_database.init_db()
    with storage_database.connect() as con:
        row = con.execute("SELECT w,h FROM canvas_nodes WHERE id=?", (structural[0]['id'],)).fetchone()
    assert (int(row['w']), int(row['h'])) == (143, 52)
