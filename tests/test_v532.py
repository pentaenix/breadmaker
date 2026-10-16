from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v532_new_canvas_is_modal_and_preserves_creation_choices():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'function openNewCanvasModal(parentId=null)' in js
    assert "$('#addCanvas').onclick=()=>openNewCanvasModal(null)" in js
    assert "$('#emptyCreateCanvas').onclick=()=>openNewCanvasModal(null)" in js
    assert "New canvas inside…',action:()=>openNewCanvasModal(canvas.id)" in js
    assert "showModal('New Canvas'" in js
    assert "openCanvasPanel('New Canvas'" not in js
    assert 'id="newCanvasName"' in js
    assert 'id="newCanvasDescription"' in js
    assert 'id="newCanvasType"' in js
    assert 'data-canvas-type="${value}"' in js
    for canvas_type in ('blank','developmental'):
        assert f"['{canvas_type}'" in js
    assert "['timeline'" not in js
    assert 'id="newCanvasParent"' in js
    assert 'id="cancelNewCanvas"' in js and 'id="createCanvasConfirm"' in js
    assert '.canvas-create-types' in css and '.canvas-create-type.active' in css
