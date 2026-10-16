from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from backend import configuration

import json
from pathlib import Path

import app


def test_v463_metro_config_is_data_driven_and_exposed(client, fresh_project):
    payload=client.get('/api/metro-view-config')
    assert payload.status_code==200
    config=payload.json()
    assert config['version']==1
    assert set(config['routing']['initial_heading_weights'])=={'0','45','90','135','180','225','270','315'}
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (4,6,0)
    assert config['spacing']['min_solid_line_mm']>0
    bootstrap=client.get('/api/bootstrap').json()
    assert bootstrap['metro_view_config']==config


def test_v463_metro_config_validation_rejects_missing_octolinear_heading(monkeypatch,tmp_path):
    source=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    source['routing']['initial_heading_weights'].pop('315')
    path=tmp_path/'metro_view.json'
    path.write_text(json.dumps(source),encoding='utf-8')
    monkeypatch.setattr(app,'METRO_VIEW_CONFIG',path)
    try:
        configuration._metro_view_config_raw()
        assert False,'invalid heading configuration should be rejected'
    except Exception as exc:
        assert getattr(exc,'status_code',None)==500


def test_v463_metro_contract_defaults_to_all_visible_lines_and_clean_span_grammar():
    js=frontend_source_from_app_path(Path('static/app.js'))
    config=json.loads(Path('config/metro_view.json').read_text(encoding='utf-8'))
    assert "lineVisibility:{}" in js
    assert "visibility[String(line.id)]!==false" in js
    assert 'filtered.length?filtered:visible' not in js
    assert 'Lines to show' in js
    assert 'Hide structural Lines' not in js
    assert 'function metroResolveLineFractions' in js
    assert "metroConfig('spacing.min_solid_line_mm'" in js
    assert 'function metroSolidRoutePath' in js
    assert "class:'metro-span-tick'" in js
    assert "stroke:paper,'stroke-width':2.25" not in js
    assert set(config['routing']['initial_heading_weights'])=={'0','45','90','135','180','225','270','315'}


def test_v463_terrain_is_procedural_collision_aware_and_full_mode_is_a_field():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert 'function metroBuildTerrainPlan' in js
    assert 'function metroPlanRiver' in js
    assert 'metroTerrainEllipseContains(o,p,clear)' in js or 'metroTerrainEllipseContains(obstacle,p,clear)' in js
    assert 'function metroCreateElevationField' in js
    assert 'function metroRenderTopographicField' in js
    assert "class:'metro-topography-contour'" in js
    assert "data-full-topography" in js
    assert '>Full topography<' in js
