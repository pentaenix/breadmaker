from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path


def test_v551_canvas_overflow_is_always_visible():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text()
    css = frontend_styles_source_from_styles_path((root / "static" / "styles.css"))
    assert tuple(map(int,(root / "VERSION").read_text().strip().split('.'))) >= (5, 5, 1)
    assert "?v=5.5.1" in html
    assert ".canvas-item-more{flex:none;width:26px;height:25px;padding:0;opacity:1}" in css
    assert ".canvas-item:hover .canvas-item-more" not in css
    assert ".canvas-rail.collapsed .canvas-item-more{display:none}" in css
