from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4102_text_style_popover_escapes_toolbar_clipping():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'data-selection-action="style"' in html
    assert "function openContextMenu(x,y,items,{anchorRect=null}={})" in js
    assert "if(anchorRect&&top+h>innerHeight-margin)top=anchorRect.top-h-6" in js
    assert '.selection-toolbar{' in css
