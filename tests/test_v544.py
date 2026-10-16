from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v544_drag_presentation_contract_and_release_metadata():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert 'Structure Order <em id="developmentalStructureScope">' in html
    assert 'id="canvasStructureBtn"' in html
    assert 'id="structureOrderToggle"' in js
    assert 'Proposed Order' not in html
    assert 'developmental-lane-drop-gap' in css
    assert 'developmental-drag-source' in css
    assert 'developmental-drag-proxy' in css
    assert 'developmentalDragOriginIndex' in js
    assert 'createDevelopmentalDragProxy' in js
    assert 'animateDevelopmentalLaneFrom' in js
    assert readme.startswith('# Breadmaker Editor 5.4.16')
    assert 'source slot stays open' in readme
    assert architecture.startswith('# Breadmaker Editor 5.4.16')
    assert 'working visual state' in architecture
