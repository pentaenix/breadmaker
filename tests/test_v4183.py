from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v4183_compact_scene_gap_indicator_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '.scene-gap-row>span{width:42%;height:3px' in css
    assert 'repeating-linear-gradient(90deg,var(--line2) 0 9px,transparent 9px 14px)' in css
    assert '.scene-gap-row.drop-target>span{width:calc(100% - 18px);height:2px' in css
