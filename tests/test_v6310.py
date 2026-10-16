from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6310_map_item_linked_anchor_and_endpoint_handle_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,10)
    assert 'placeholder="Optional description"' not in js
    assert 'function mapItemStructuralReferences(item)' in js
    assert "['chapter','scene'].includes(type)" in js
    assert 'const structuralRefs=mapItemStructuralReferences(item)' in js
    assert 'renderReferenceRows(displayRefs)' in js
    assert 'wireReferenceDeletes(displayRefs' in js
    assert "if(overlap?.item?.item_type==='span')continue" in js
