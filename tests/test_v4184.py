from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v4184_scene_navigation_presentation_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '<span class="scene-short">${escapeHtml(sceneRailLabel(scene))}</span>' in js
    assert ".book-nav.collapsed .scene-short{display:block}" in css
    assert ".book-nav.collapsed .scene-outline-row{min-height:40px" in css
    assert "const title=activeDocument()?.title||'Manuscript';" in js
    assert "className='ghost-scene-title scene-view-boundary'" in js
    assert "className='ghost-scene-end scene-view-boundary'" in js
