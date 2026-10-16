from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from backend import configuration

import json
from pathlib import Path

import app


def test_v46_feature_flag_is_data_driven_and_bootstrap_exposes_it(client, fresh_project):
    payload=client.get('/api/features')
    assert payload.status_code==200
    assert payload.json()=={'version':1,'features':{'maps_metro_view':True}}
    bootstrap=client.get('/api/bootstrap').json()
    assert bootstrap['features']['maps_metro_view'] is True


def test_v46_feature_flag_validation_rejects_non_boolean_values(monkeypatch, tmp_path):
    config=tmp_path/'features.json'
    config.write_text(json.dumps({'version':1,'features':{'maps_metro_view':'yes'}}),encoding='utf-8')
    monkeypatch.setattr(app,'FEATURE_FLAGS_CONFIG',config)
    try:
        configuration._feature_flags_raw()
        assert False, 'invalid feature flags should be rejected'
    except Exception as exc:
        assert getattr(exc,'status_code',None)==500


def test_v46_metro_view_contract_is_a4_svg_first_and_read_only():
    js=frontend_source_from_app_path(Path('static/app.js'))
    html=Path('static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    features=json.loads(Path('config/features.json').read_text(encoding='utf-8'))
    assert features['features']['maps_metro_view'] is True
    assert 'id="mapMetroViewBtn"' in html and 'metro-view-button' in html and '/static/map-icon.svg' in css
    assert "if(!featureEnabled('maps_metro_view'))return" in js
    assert "orientation:'portrait'" in js
    assert "portrait?{w:210,h:297}:{w:297,h:210}" in js
    assert 'function metroCoreLine' in js
    assert 'function metroBuildPosterModel' in js
    assert 'function metroRenderPoster' in js
    assert 'function metroDownloadSvg' in js
    assert 'async function metroDownloadPng' in js
    assert 'function metroPrintPoster' in js
    assert "Metro View is a read-only poster. It preserves story topology" in js
    assert '.metro-poster-svg' in css and '.modal-card.metro-modal' in css
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (4,6,0)
