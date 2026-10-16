from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v5412_canvas_interaction_cleanup_static_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))

    # Drawing no longer flashes a default-size preview at pointer-down.
    draw = js.split('function beginCanvasCardDraw(event,shape){', 1)[1].split('function canvasNodePayload', 1)[0]
    assert 'canvas-card-draw-preview ${shape} hidden' in draw
    assert 'paint(start)' not in draw
    assert "preview.classList.remove('hidden')" in draw
    assert "cancelCanvasCardTool();setCanvasMode('pointer')" in draw
    assert '.node-title`)?.focus()' not in draw

    # Empty-canvas connector drops no longer auto-create Cards.
    assert 'createConnectedCanvasNode' not in js
    connector = js.split('function startConnector(event,node,port){', 1)[1].split('function nodeMatchesLegend', 1)[0]
    assert "if(target&&target.node.id!==node.id)await makeEdge" in connector
    assert '/nodes' not in connector

    # Text-only Cards keep the compact centered grab pill; deprecated Oval/Diamond Cards are gone.
    assert '.canvas-node.text .node-drag-strip{' in css
    assert '.canvas-node.oval' not in css
    assert '.canvas-node.diamond' not in css
    assert 'width:30px;' in css
    assert 'border-radius:999px;' in css
