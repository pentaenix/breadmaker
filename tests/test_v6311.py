from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6311_line_connector_threshold_and_station_priority_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,11)
    assert 'MAP_LINE_CONNECT_HOVER_MS=300' in js
    assert 'MAP_LINE_CONNECT_MIN_SEGMENT_STATION_WIDTHS=5' in js
    assert "stationOwnsHover=e=>!!e.target?.closest?.('.map-stop')" in js
    assert "if(stationOwnsHover(e)){clearShow();clearHide();pending=null;hideAll();return}" in js
