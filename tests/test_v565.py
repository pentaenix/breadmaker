from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v565_release_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 5)
    html=(root/'static/index.html').read_text()
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert '?v=5.6.5' in html
    assert "const THEME_CYCLE=['light','dusk','dark']" in js
    assert ':root[data-theme=dusk]' in css
    assert '--index-bg:#000' in css
    assert 'canvasZoomFit' in html and 'view-zoom-fit' in html
    assert '.canvas-rail{width:236px;min-width:196px}' in css
    assert "{node_type:'label',w:40,h:40,title:value,body:'',color:'',text_color:''}" in js
