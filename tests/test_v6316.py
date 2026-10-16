from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6316_maps_rail_background_owns_collection_context_menu():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,16)
    assert "$('#mapRail').oncontextmenu=e=>" in js
    assert "e.target.closest('.map-tree-map-row,.map-tree-line-row')" in js
    assert 'openMapCollectionMenu(e)' in js
    assert "function mapCollectionMenuItems(){return[{label:'Add Map'" in js
    assert "{label:'Import Map…'" in js
