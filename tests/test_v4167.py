from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4167_marker_description_hover_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'id="markerPanelHoverCard"' in html
    assert "const description=String(marker?.description||'').trim();if(!description)return;" in js
    assert '},300);' in js
    assert 'row.onmouseenter=()=>queueMarkerPanelHover(marker,row)' in js
    assert '.marker-panel-hover-card{' in css
    assert '.marker-panel-hover-description{' in css
    assert 'marker-panel-hover-head' not in js
