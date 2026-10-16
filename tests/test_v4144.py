from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4144_version_and_selection_toolbar_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert "toast(`Imported ${result.imported.toLocaleString()} Cut${result.imported===1?'':'s'}`);" in js
    assert 'existing Cuts kept' not in js
    assert 'state.manuscriptPointerSelecting=true' in js
    assert 'showSelectionToolbarAfterPointer' in js
    assert 'selection.isCollapsed||state.manuscriptPointerSelecting' in js
    assert 'user-select:none' in css and '-webkit-user-select:none' in css
