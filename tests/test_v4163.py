from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4163_markup_selection_speed_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'data-selection-action="highlight"' in html
    assert html.index('data-selection-action="highlight"') < html.index('data-selection-action="comment"')
    assert 'id="selectionHighlightPicker"' in html
    assert 'id="selectionHighlightMarkerList"' in html
    assert 'id="selectionHighlightNewMarker"' in html
    assert 'lastUsedMarkupMarkerId' in js
    assert 'function markerPickerOrder()' in js
    assert "localStorage.setItem('breadmaker-last-markup-marker'" in js
    assert 'function openSelectionHighlightPicker(items,anchorRect)' in js
    assert 'marker-empty-state' in js and 'markerEmptyNew' in js
    assert '.marker-list.empty{display:grid;place-items:center' in css
    assert '.selection-highlight-marker-list{min-height:0;max-height:238px;overflow-y:auto;overflow-x:hidden' in css
    assert '.markup-gutter{left:-27px}' in css
