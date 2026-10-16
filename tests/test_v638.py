from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v638_segment_aware_line_connector_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()

    assert tuple(map(int,version.split('.'))) >= (6,3,8)
    assert 'MAP_LINE_CONNECT_HOVER_MS=300' in js
    assert 'MAP_LINE_CONNECT_MIN_SEGMENT_STATION_WIDTHS=' in js
    assert 'function mapLineConnectSegments(' in js
    assert "entry.item.item_type==='stop'" in js
    assert 'centerAxis=(startAxis+endAxis)/2' in js
    assert 'mapLinePositionAtDisplayAxis(plan,centerAxis)' in js
    assert 'function renderMapLineConnectHandles(' in js
    assert "group.addEventListener('pointermove',scheduleShow)" in js
    assert 'function mapLineDisplayAxisAtPosition(' in js
    assert 'coord=mapAxisCoordinate(layout,mapLineDisplayAxisAtPosition(info.plan,p),info.lane)' in js
