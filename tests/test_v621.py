from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v621_maps_view_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'id="mapViewRuler"' not in html
    assert 'id="mapTextSize"' in html and 'id="mapFont"' in html
    assert 'class="map-view-cycle-row"' in html
    assert "{label:'Ruler',children:mapRulerMenuChildren(map)}" in js
    assert "$('#mapViewOrientation')?.addEventListener('click'" in js
    assert "$('#mapViewLayout')?.addEventListener('click'" in js
    assert "$('#mapViewLabels')?.addEventListener('click'" in js
    assert "applyMapTextSize" in js and "applyMapFont" in js
    assert '.maps-sidebar{width:236px;min-width:236px;flex:0 0 236px' in css
    assert '@media(max-width:900px){.map-toolbar{overflow-x:auto}}' in css
    assert '.map-tree-lock.unlocked' in css


def test_v621_version_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (6, 2, 1)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert '/static/styles.css?v=6.2.1' in html
    assert '/static/app.js?v=6.2.1' in html
