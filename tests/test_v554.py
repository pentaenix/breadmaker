from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path


def test_v554_collapsed_canvas_rail_hides_overflow_even_with_shared_important_display():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text()
    css = frontend_styles_source_from_styles_path((root / "static" / "styles.css"))
    assert tuple(map(int,(root / "VERSION").read_text().strip().split('.'))) >= (5, 5, 4)
    assert "?v=5.5.4" in html
    assert ".manuscript-item-more{" in css and "display:grid!important" in css
    assert ".canvas-rail.collapsed .canvas-item-more{display:none!important}" in css
