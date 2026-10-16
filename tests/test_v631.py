from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v631_line_menu_freezes_anchor_before_async_work():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    assert "const menuAnchor=e.type==='contextmenu'?{kind:'point'" in js
    anchor_pos=js.index("const menuAnchor=e.type==='contextmenu'?{kind:'point'")
    select_pos=js.index("if(state.currentMap?.id!==map.id)await selectMap(map.id)",anchor_pos)
    assert anchor_pos < select_pos
    assert "e.currentTarget.getBoundingClientRect();openContextMenu" not in js[anchor_pos:select_pos+500]
