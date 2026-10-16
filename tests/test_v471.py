from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v471_maps_toolbar_and_single_collapse_control_contract():
    html=Path('static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'id="mapSidebarToggle"' not in html
    assert html.count('id="toggleMapRail"') == 1
    assert js.count("$('#toggleMapRail')?.addEventListener('click',toggleMapRail)") == 1
    fit=html.index('id="mapZoomFit"')
    view=html.index('id="mapViewBtn"')
    metro=html.index('id="mapMetroViewBtn"')
    assert fit < view < metro


def test_v471_double_click_focus_and_color_autosave_contract():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert 'function focusMapLine(lineId)' in js
    assert 'lineRow.ondblclick=async e=>' in js
    assert 'focusMapLine(line.id);openMapLineInspector(line.id)' in js
    assert "route.ondblclick=e=>{e.preventDefault();e.stopPropagation();if(state.mapPendingConnection)return;focusMapLine(line.id);openMapLineInspector(line.id)}" in js
    assert "route.ondblclick=e=>{if(state.mapPendingConnection||line.layout_locked)" not in js
    assert 'function scheduleMapLineColorSave' in js
    assert 'function flushMapLineColorSave' in js
    assert 'Color changes are applied automatically.' in js
    assert "text.oninput=()=>{const color=text.value.trim();" in js
    assert "const payload={name:$('#mapLineName').value.trim(),description:" in js
