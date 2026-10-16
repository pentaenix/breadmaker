from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v636_map_line_center_connector_threshold_is_half_of_v635():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()

    assert tuple(map(int,version.split('.'))) >= (6,3,6)
    assert 'MAP_LINE_CONNECT_HOVER_MS=300' in js
    assert 'MAP_LINE_CONNECT_MIN_SEGMENT_STATION_WIDTHS=' in js
    assert 'minScreenLength=mapStationOuterRadius()*2*MAP_LINE_CONNECT_MIN_SEGMENT_STATION_WIDTHS' in js
    assert 'routeEdge=Math.max(0,MAP_ROUTE_WIDTH/2-1)' in js
