from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v543_structural_link_contract_and_release_metadata():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    assert 'function structuralNodeReference(node)' in js
    assert 'structural_binding:true' in js
    assert "r.structural_binding?' Structural link'" in js or "r.structural_binding?' · Structural link'" in js
    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
