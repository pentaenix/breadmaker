from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6315_station_reflow_is_local_and_spacing_is_reduced():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,15)
    assert 'MAP_STATION_SPACING_FACTOR=.70' in js
    assert 'function mapResolveStationAxes(' in js
    assert 'A roomy gap is' in js
    assert 'for(let i=movingIndex-1;i>=0;i--)' in js
    assert 'for(let i=movingIndex+1;i<values.length;i++)' in js
