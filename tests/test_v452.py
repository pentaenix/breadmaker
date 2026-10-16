from __future__ import annotations
from pathlib import Path
from source_contracts import frontend_styles_source_from_styles_path
from source_contracts import frontend_source_from_app_path


def test_v452_maps_contract_uses_item_footprint_gap_local_line_layout_and_drag_connect_handle():
    js=frontend_source_from_app_path('static/app.js')
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    assert 'MAP_MIN_VISIBLE_ROUTE_SCREEN_GAP=10' in js
    assert 'function mapLineRenderPlan' in js
    assert 'previousRight+minRoute' in js
    assert 'linePlans=new Map' in js
    assert 'mapLayoutBreakpoints' not in js
    assert 'map-station-connect-handle' in js
    assert 'Drag to connect this Station to another Line' in js
    assert "{label:'Connect to Another Line…',action:()=>beginMapItemConnection(line,item)}" in js
    assert 'rotate(90' in js and 'map-ruler-chapter-label' in js
    assert '.map-station-connect-handle' in css
