from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v637_map_delete_shortcuts_and_rendered_connector_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()

    assert tuple(map(int,version.split('.'))) >= (6,3,7)
    assert "event.key==='Delete'||event.key==='Backspace'" in js
    assert "if(state.selectedMapJunctionId)" in js
    assert "async function deleteMapJunction(junction){await withStructuralUndo('Delete Map junction'" in js
    assert "confirmDialog('Delete Junction'" not in js
    assert 'function mapLineConnectSegments(' in js
    assert 'screenLength=(endAxis-startAxis)*Math.max(.01,zoom)' in js
    assert "group.addEventListener('pointerover',scheduleShow)" in js
    assert "group.addEventListener('pointermove',scheduleShow)" in js
    assert "group.addEventListener('pointerleave',scheduleHide)" in js
