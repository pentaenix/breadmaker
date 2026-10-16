from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v555_canvas_collapsed_labels_use_generic_prefix_token():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    html = (root / 'static' / 'index.html').read_text()
    assert tuple(map(int,(root / 'VERSION').read_text().strip().split('.'))) >= (5, 5, 5)
    assert '?v=5.5.5' in html
    assert 'function canvasRailLabel(canvas)' in js
    assert r"/^(?:Canvas|Chapter)\s+(\d+|[A-Za-z])(?=$|[\s:._-])/i" in js
    assert '${escapeHtml(canvasRailLabel(canvas))}' in js
