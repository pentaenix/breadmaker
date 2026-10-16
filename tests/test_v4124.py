from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4124_pdf_checkbox_cleanup_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    for helper in (
        'Number manuscript pages; the cover is not counted.',
        'Keeps the exported manuscript easy to navigate.',
        'Add a simple presentation page before the manuscript.',
    ):
        assert helper not in js
    assert 'position:absolute;left:50%;top:43%' in css
    assert 'translate(-50%,-50%) rotate(45deg)' in css
