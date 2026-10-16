from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v6317_map_details_populate_and_collapsed_rail_contracts():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,17)
    assert "await loadMapPopulateCatalog()" in js
    assert "Populate options could not be loaded." in js
    assert "showModal('Map Details'" in js
    assert 'mapDetailsBackgroundMode' in js
    assert 'mapDetailsBackgroundMode' in js and 'dynamic.html.text.option.' in js
    assert 'value="custom"' in js and 'dynamic.html.text.option.' in js
    assert 'saveMapInspector' not in js
    assert 'duplicateMapInspector' not in js
    assert 'deleteMapInspector' not in js
    # Auto arrange is no longer exposed from Map menus.
    menu=js[js.index('function mapMapMenu'):js.index('async function runOnMap')]
    background=js[js.index('function mapBackgroundMenu'):js.index('function openMapInspector')]
    assert 'Auto Arrange' not in menu
    assert 'Auto Arrange' not in background
    assert 'map-tree-map-short' in js and 'map-tree-line-short' in js
    assert 'map-tree-line-badge' not in js
    assert '.canvas-rail.collapsed .canvas-item.active{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}' in css
    assert '.maps-sidebar.collapsed .map-tree-map-row.active,.maps-sidebar.collapsed .map-tree-line-row.active{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}' in css
    assert 'stroke-opacity:.09' in css
