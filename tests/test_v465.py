from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from backend import configuration

import json
from pathlib import Path


def test_v465_version_and_route_quality_config():
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (4,6,5)
    cfg=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    routing=cfg['routing']
    assert routing['candidate_count'] >= 20
    assert routing['max_segments'] >= 16
    assert len([v for v in routing['strategy_weights'].values() if v > 0]) >= 4
    assert routing['scoring']['out_of_bounds'] > routing['scoring']['snake'] > 0
    assert routing['scoring']['sharp_turn'] > 0
    assert routing['connections']['departure_mm'] >= 3
    assert 0.7 <= cfg['appearance']['connection_width_factor'] < 1
    assert 0 < cfg['poster']['legend_bottom_margin_mm'] <= 7


def test_v465_long_line_fallback_is_not_row_serpentine_and_routes_have_quality_gate():
    js=frontend_source_from_app_path(Path('static/app.js'))
    fallback=js[js.index('function metroFallbackRoute'):js.index('function metroPlanLineRoute')]
    assert 'runGap' not in fallback
    assert 'broad-diagonal-a' in fallback
    assert 'open-spiral-a' in fallback
    assert 'perimeter-' in fallback
    assert 'metroSnakePenalty' in js
    assert 'metroSharpTurnPenalty' in js
    assert 'metroRouteInsideBox' in js
    assert 'metroRouteCandidateScore' in js
    assert 'metroStrategyName' in js


def test_v465_connectors_get_independent_departure_geometry_and_slightly_smaller_weight():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert 'function metroConnectorRoute' in js
    assert 'fromTangent=metroRouteTangent' in js
    assert 'departure=Math.max' in js
    assert 'route,[fromRef.coord,depart' not in js  # guard against accidental malformed edit
    render=js[js.index('function metroRenderPoster'):js.index('function metroPosterFileBase')]
    assert 'metroOctoPath(c.route||[c.from,c.to])' in render
    assert "appearance.connection_width_factor" in render


def test_v465_preview_uses_svg_intrinsic_a4_ratio_and_map_icon_is_centered():
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    assert '.metro-view-button{display:inline-flex!important;align-items:center;justify-content:center}' in css
    assert '.metro-poster-svg{height:auto!important;max-height:none!important}' in css
    assert '.metro-poster-svg[data-paper="portrait"]' in css
    assert '.metro-poster-svg[data-paper="landscape"]' in css


def test_v465_config_validation_rejects_invalid_connector_width(monkeypatch,tmp_path):
    import app
    source=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    source['appearance']['connection_width_factor']=1.2
    path=tmp_path/'metro_view.json';path.write_text(json.dumps(source),encoding='utf-8')
    monkeypatch.setattr(app,'METRO_VIEW_CONFIG',path)
    try:
        configuration._metro_view_config_raw()
        assert False,'invalid connector width should fail validation'
    except Exception as exc:
        assert getattr(exc,'status_code',None)==500
