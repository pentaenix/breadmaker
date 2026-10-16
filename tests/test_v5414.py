from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v5414_canvas_creation_mode_and_copy_paste_static_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))

    assert 'function canvasCreationToolActive()' in js
    assert 'function copyCanvasCards()' in js
    assert 'function pasteCanvasCards()' in js
    assert 'canvasPointerClient' in js
    assert "key==='c'" in js and "key==='v'" in js
    assert "node.node_type==='free'&&!isDevelopmentalStructureCard(node)" in js
    assert 'anchor.x-groupW/2' in js and 'anchor.y-groupH/2' in js
    assert 'from_source_id' in js and 'to_source_id' in js

    # Existing nodes/figures/connectors must be pointer-inert while a draw tool is armed.
    assert '.canvas-stage.card-create-mode #nodes .canvas-node' in css
    assert '.canvas-stage.card-create-mode #figures .canvas-node' in css
    assert '.canvas-stage.card-create-mode #edgeLayer' in css
    assert '.canvas-stage.figure-create-mode #nodes .canvas-node' in css
    assert '.canvas-view.canvas-creation-mode #developmentalLaneViewport' in css
    assert 'pointer-events:none!important' in css
