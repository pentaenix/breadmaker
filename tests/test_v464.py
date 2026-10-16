from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from backend import configuration

import json
from pathlib import Path


def test_v464_metro_toolbar_and_line_selector_contract():
    html=Path('static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(Path('static/app.js'))
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    assert html.index('id="mapZoomFit"') < html.index('id="mapMetroViewBtn"')
    assert 'metro-view-icon' in html and "url('/static/map-icon.svg')" in css
    assert Path('static/map-icon.svg').exists()
    assert 'Lines to show' in js and 'data-metro-line-toggle' in js
    assert 'Hide structural Lines' not in js
    assert "lineVisibility:{}" in js


def test_v464_connections_are_solid_and_labels_avoid_network_geometry():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert "class:'metro-connection'" in js
    metro=js[js.index("const connectors=svgNode('g',{class:'metro-connections'}"):js.index("const stationsGroup=svgNode('g',{class:'metro-stations'}")]
    assert "stroke-dasharray" not in metro
    assert 'function metroLabelNetworkHits' in js
    assert 'metroLabelNetworkHits(model,rect)' in js
    assert "edition.textContent='BREADMAKER METRO VIEW'" not in js


def test_v464_scale_spacing_and_separation_are_data_driven():
    cfg=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert cfg['spacing']['min_solid_line_mm'] >= 3.0
    assert cfg['routing']['segments']['line_separation_mm'] >= 5.0
    assert cfg['routing']['scale']['coverage_exponent'] > 0
    assert "coverage=Math.pow(metroClamp(bounds.coverage,0,1)" in js
    assert 'metroRoutesDistance([current,endpoint],route)<lineSeparation' in js


def test_v464_full_topography_uses_elevation_field_not_parallel_waves_or_park_ovals():
    cfg=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert 'function metroCreateElevationField' in js
    assert 'function metroElevationAt' in js
    assert 'function metroContourSegments' in js
    assert "class:'metro-topography-contour'" in js
    assert 'metroElevationAt(elevationField,p)' in js
    assert cfg['terrain']['topography']['contour_levels'] >= 7
    assert 'parks' not in cfg['terrain']
    assert "g.append(svgNode('ellipse',{cx:park.cx" not in js



def test_v464_config_validation_covers_scale_separation_and_topography(monkeypatch,tmp_path):
    import app
    source=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    source['routing']['segments']['line_separation_mm']=0
    path=tmp_path/'metro_view.json';path.write_text(json.dumps(source),encoding='utf-8')
    monkeypatch.setattr(app,'METRO_VIEW_CONFIG',path)
    try:
        configuration._metro_view_config_raw()
        assert False,'invalid separation should fail validation'
    except Exception as exc:
        assert getattr(exc,'status_code',None)==500
