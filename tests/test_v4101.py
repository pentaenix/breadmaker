from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v4101_text_style_selection_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert "function selectionItems(rangeOverride=null)" in js
    assert "selectionItems(state.lastManuscriptRange)" in js
    assert 'data-selection-action="style"' in html
    assert "if(action==='style')" in js
    assert "Aa  Text style" in js
    assert "manuscriptStyleMenuItems" in js
