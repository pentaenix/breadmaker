from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6313_map_placement_and_failure_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,13)
    assert "const ordered=[...items].sort((a,b)=>(a.start-b.start)" in js
    assert "line.provenance==='source'?" not in js[js.index('function mapLineRenderPlan'):js.index('function mapAxisDistance')]
    assert "Stations may legitimately sit over Spans" in js
    assert 'mapStationPlacementUpdates' in js
    assert "newIndex<oldIndex?'right':newIndex>oldIndex?'left'" in js
    assert 'mapConnectionDropProblemAt' in js
    assert 'Connections must end on a different Line.' in js
    assert 'Drop the connection on another Line, Station, or Span.' in js
    assert "i18nText('map.span_edge_blocked'" in js
    assert "i18nText('map.line_edge_blocked'" in js
