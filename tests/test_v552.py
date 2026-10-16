from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v552_toolbar_detail_affordances_and_release_stamp():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text()
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    assert tuple(map(int,(root / 'VERSION').read_text().strip().split('.'))) >= (5, 5, 2)
    assert '?v=5.5.2' in html
    assert 'id="editActiveDocumentBtn"' in html
    assert 'id="editCanvasDetailsBtn"' in html
    assert 'id="canvasName"' in html and 'readonly' in html
    assert "$('#editActiveDocumentBtn').onclick" in js
    assert "$('#editCanvasDetailsBtn').onclick" in js
    assert '.toolbar-title-pencil' in css
