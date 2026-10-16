from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v642_collapsed_map_lines_are_active_map_color_swatches():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,4,2)
    assert "const showLines=state.mapRailExpanded?!collapsed:state.currentMap?.id===map.id" in js
    assert '.maps-sidebar.collapsed .map-tree-line-short{display:none}' in css
    assert '.maps-sidebar.collapsed .map-tree-line-dot{display:block;width:26px;height:8px;border-radius:999px}' in css
