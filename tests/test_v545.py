from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v545_ui_consistency_contract_and_release_metadata():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert 'function developmentalTileTextColor(node)' in js
    assert 'markerTextColor(color)' in js
    assert 'structure-auto-contrast-hint' not in js
    assert "canvas-developmental-desk-label" not in js
    assert 'function jumpDevelopmentalPreviewToFirstChange()' in js
    assert 'requestAnimationFrame(()=>requestAnimationFrame(jumpDevelopmentalPreviewToFirstChange))' in js
    assert 'canvas-version-list-row' in js and 'canvas-version-more' in js
    assert 'canvasVersionPrev' not in js and 'canvasVersionNext' not in js
    assert 'canvas-version-list-row.active' in css
    assert '#canvasStructureBtn.active' in css
    assert 'aria-pressed="false" aria-label="Structure"' in html
    assert 'id="reviewStructureChanges"' in js
    assert readme.startswith('# Breadmaker Editor 5.4.16')
    assert architecture.startswith('# Breadmaker Editor 5.4.16')
