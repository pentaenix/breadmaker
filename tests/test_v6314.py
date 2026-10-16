from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6314_station_reflow_is_deferred_until_release():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,14)
    assert 'previewMapStationPlacement' not in js
    assert 'Crowded\n      // Station reflow is calculated and committed only after pointer release.' in js
    assert 'const placement=mapStationPlacementUpdates(line,item,targetAxis,newStart,plan,mode)' in js
