from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import io
import json
import re
from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]
APP = frontend_source_from_app_path(ROOT / 'static/app.js')
CSS = frontend_styles_source_from_styles_path((ROOT / 'static/styles.css'))
INDEX = (ROOT / 'static/index.html').read_text(encoding='utf-8')


def _main_tab(client):
    boot = assert_ok(client.get('/api/bootstrap'))
    return next(tab for tab in boot['tabs'] if tab.get('is_manuscript'))




def test_v721_line_figures_persist_free_angle_geometry(client, fresh_project):
    cv = assert_ok(client.post('/api/canvases', json={'name': 'Free angle figures'}))
    line = assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes", json={
        'node_type': 'figure', 'shape': 'line', 'x': 100, 'y': 120, 'w': 360, 'h': 220,
        'figure_line_x1': 0.08, 'figure_line_y1': 0.88,
        'figure_line_x2': 0.94, 'figure_line_y2': 0.12,
    }))
    assert line['shape'] == 'line'
    assert abs(float(line['figure_line_y1']) - .88) < 1e-9
    assert abs(float(line['figure_line_y2']) - .12) < 1e-9

    assert_ok(client.patch(f"/api/nodes/{line['id']}", json={
        'x': 80, 'y': 90, 'w': 410, 'h': 260,
        'figure_line_x1': .02, 'figure_line_y1': .10,
        'figure_line_x2': .98, 'figure_line_y2': .92,
    }))
    data = assert_ok(client.get(f"/api/canvases/{cv['id']}"))
    saved = next(node for node in data['nodes'] if node['id'] == line['id'])
    assert abs(float(saved['figure_line_y1']) - .10) < 1e-9
    assert abs(float(saved['figure_line_y2']) - .92) < 1e-9
    assert float(saved['h']) == 260

    project = fresh_project['project']
    response = client.get(f"/api/projects/{project['id']}/export-json")
    assert response.status_code == 200, response.text
    payload = json.loads(response.content)
    canvas = next(c for c in payload['canvases'] if c['name'] == 'Free angle figures')
    exported = next(n for n in canvas['nodes'] if n['type'] == 'figure' and n['shape'] == 'line')
    assert exported['line_x1'] == .02 and exported['line_y1'] == .10
    assert exported['line_x2'] == .98 and exported['line_y2'] == .92


def test_v721_fragment_import_preserves_line_endpoints(client, fresh_project):
    fragment = {
        'format': 'breadmaker-fragment', 'schema_version': '1.0', 'name': 'Diagonal line',
        'canvases': [{'id': 'canvas:line', 'name': 'Imported line', 'nodes': [{
            'id': 'figure:line', 'type': 'figure', 'shape': 'line',
            'line_x1': .12, 'line_y1': .91, 'line_x2': .87, 'line_y2': .07,
            'position': {'x': 90, 'y': 110, 'width': 330, 'height': 210},
        }]}],
    }
    files = {'file': ('line.breadmaker.json', io.BytesIO(json.dumps(fragment).encode()), 'application/json')}
    response = client.post('/api/json-import', files=files, data={'context': '{}', 'target_choices': '{}'})
    assert response.status_code == 200, response.text
    boot = assert_ok(client.get('/api/bootstrap'))
    cv = next(c for c in boot['canvases'] if c['name'] == 'Imported line')
    data = assert_ok(client.get(f"/api/canvases/{cv['id']}"))
    line = next(n for n in data['nodes'] if n['node_type'] == 'figure')
    assert line['shape'] == 'line'
    assert abs(float(line['figure_line_x1']) - .12) < 1e-9
    assert abs(float(line['figure_line_y1']) - .91) < 1e-9
    assert abs(float(line['figure_line_x2']) - .87) < 1e-9
    assert abs(float(line['figure_line_y2']) - .07) < 1e-9


def test_v721_chapter_population_spans_full_percentage_axis_and_sync_repairs_old_bounds(client, fresh_project):
    tab = _main_tab(client)
    for title in ('Zero', 'Middle', 'Hundred'):
        assert_ok(client.post('/api/chapters', json={'title': title, 'tab_id': tab['id']}))
    story = assert_ok(client.post('/api/maps', json={'name': 'Percentage ruler', 'ruler': 'percent'}))
    populated = assert_ok(client.post(f"/api/maps/{story['id']}/populate", json={'template_id': 'chapters'}))
    detail = assert_ok(client.get(f"/api/maps/{story['id']}"))
    line = next(row for row in detail['lines'] if row['id'] == populated['line_id'])
    assert abs(float(line['schematic_start']) - 0.0) < 1e-9
    assert abs(float(line['schematic_end']) - 1.0) < 1e-9
    source_items = [item for item in line['items'] if item.get('source_key')]
    assert abs(float(source_items[0]['schematic_start']) - 0.0) < 1e-9
    assert abs(float(source_items[-1]['schematic_start']) - 1.0) < 1e-9

    # Simulate a pre-7.2.1 populated line that had the old 5%-95% envelope.
    assert_ok(client.patch(f"/api/map-lines/{line['id']}", json={
        'layout_locked': False, 'planned_start': .05, 'planned_end': .95,
        'schematic_start': .05, 'schematic_end': .95,
    }))
    assert_ok(client.patch(f"/api/map-lines/{line['id']}", json={'layout_locked': True}))
    assert_ok(client.post(f"/api/map-lines/{line['id']}/sync"))
    repaired = assert_ok(client.get(f"/api/maps/{story['id']}"))
    repaired_line = next(row for row in repaired['lines'] if row['id'] == line['id'])
    assert abs(float(repaired_line['schematic_start']) - 0.0) < 1e-9
    assert abs(float(repaired_line['schematic_end']) - 1.0) < 1e-9


def test_v721_frontend_contract_follows_existing_figure_conventions():
    assert 'figure-line-handle start' in APP and 'figure-line-handle end' in APP
    assert 'data-line-endpoint="start"' in APP and 'data-line-endpoint="end"' in APP
    assert 'lineFigureGeometryFromEndpoints' in APP
    assert "transform:`rotate(${Math.atan2(dy,dx)*180/Math.PI}deg)`" in APP
    assert 'needsConfirm=hasStructural' in APP
    assert 'id="figureFillChoice"' in APP and 'id="figureStrokeChoice"' in APP
    assert '<option value="default"' in APP and '<option value="custom"' in APP
    assert "fillInput?.classList.toggle('hidden',choice!=='custom')" in APP
    assert "strokeInput.classList.toggle('hidden',choice!=='custom')" in APP
    assert '.figure-line-handle{' in CSS
    assert '.canvas-figure.line:hover .figure-line-handle' in CSS
    assert 'closeSiblingMenus();row.classList.add(\'submenu-open\')' in APP
    # English should use the same native/English two-line presentation as every other locale.
    assert '<small>${escapeHtml(english)}</small>' in APP
    assert '<small>${escapeHtml(code)}</small>' not in APP


def test_v721_all_locales_use_line_width_and_keep_parity():
    expected = {
        'en': 'Line width',
        'es': 'Grosor de línea',
        'fr': 'Épaisseur du trait',
        'it': 'Spessore della linea',
        'ja': '線の太さ',
        'pt-BR': 'Espessura da linha',
        'zh-CN': '线宽',
    }
    en = json.loads((ROOT / 'locales/en.json').read_text(encoding='utf-8'))
    token = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for code, label in expected.items():
        data = json.loads((ROOT / 'locales' / f'{code}.json').read_text(encoding='utf-8'))
        assert data['strings']['canvas.figure.border_width'] == label
        assert set(data['strings']) == set(en['strings'])
        assert data.get('source_index', {}) == en.get('source_index', {})
        for key, value in en['strings'].items():
            assert sorted(token.findall(value)) == sorted(token.findall(data['strings'][key])), (code, key)
