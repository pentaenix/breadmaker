from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4162_markup_gutter_uses_color_swatches_and_hover_flyer():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'id="markupHoverCard"' in html
    assert 'function renderMarkupGutterSwatches' in js
    assert "annotations.filter(a=>a.annotation_type==='highlight'&&a.label_id&&a.label_color)" in js
    assert 'legacyEditorial' not in js
    assert 'queueMarkupHover(marker,swatch,blockId)' in js
    assert '},300);' in js
    assert 'navigateMarker(marker.id)' in js
    assert '.markup-gutter-swatch{' in css
    assert '.markup-hover-card{' in css
    assert '.markup-hover-dot{' in css
    assert '.legacy-annotation-gutter{left:-96px}' not in css
