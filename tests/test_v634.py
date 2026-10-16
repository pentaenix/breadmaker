from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v634_map_line_connection_affordance_and_edge_item_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    assert 'function renderMapLineConnectHandles(' in js
    assert 'function wireMapLineConnectionDrag(' in js
    assert 'function createMapLinePointConnection(' in js
    assert "from_line_id:line.id,from_edge:'point'" in js
    assert "class:`map-line-handle${overlap?' item-overlap':''}`" in js
    assert '.map-line-handle.item-overlap{fill:none;pointer-events:stroke' in css
    assert '.map-line-connect-affordance{opacity:0;pointer-events:none' in css
    assert '.map-line-connect-affordance.visible{opacity:1;pointer-events:auto}' in css
