from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v4195_compact_scene_gap_bookmark_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '.book-nav.collapsed .scene-gap-row .scene-gap-bookmark-icon' in css
    assert 'display:grid!important' in css
    assert '.book-nav.collapsed .scene-gap-row:has(.scene-gap-bookmark-icon)>.scene-gap-dashes{width:16px}' in css
