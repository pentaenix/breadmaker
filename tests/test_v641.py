from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v641_release_contract():
    version=(ROOT/'VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (6,4,1)
    app=frontend_source_from_app_path(ROOT/'static'/'app.js')
    html=(ROOT/'static'/'index.html').read_text()
    assert "MAP_RULER_SCALE_KEY='breadmaker-map-ruler-scale'" in app
    assert "naturalAxisLength*normalizeMapRulerScale(state.mapRulerScale)" in app
    assert 'mapStationRenderScale' in app
    assert 'mapStationSpacingCompression' in app
    assert 'id="mapRulerScale"' in html
    assert 'min="60" max="180" step="5"' in html
