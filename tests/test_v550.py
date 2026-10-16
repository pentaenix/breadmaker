from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v550_release_and_canvas_ui_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text()
    css = frontend_styles_source_from_styles_path((root / 'static' / 'styles.css'))
    js = frontend_source_from_app_path(root / 'static' / 'app.js')

    assert tuple(map(int,(root / 'VERSION').read_text().strip().split('.'))) >= (5, 5, 0)
    assert '?v=5.5.0' in html
    assert 'data-initial-view' in js
    assert "document.documentElement.dataset.initialView=view" in html
    assert 'html[data-initial-view="canvas"] #canvasView' in css

    assert 'id="canvasSnapToggle"' in html
    assert html.index('id="canvasZoomFit"') < html.index('id="canvasTextSize"') < html.index('id="canvasFont"')
    assert 'canvasSnap: true' in js
    assert 'snap:!!state.canvasSnap' in js
    assert 'function nearestCanvasSnap' in js
    assert 'CANVAS_SNAP_GRID=18' in js
    assert 'canvas-snap-guide' in css

    assert 'function canvasMenuItems(canvas)' in js
    assert 'canvas-item-more manuscript-item-more' in js
    assert 'queueCanvasDetailsHover(canvas,row)' in js
    assert "chapter-details-hover-card canvas-details-hover-card" in js

    assert "$('#canvasStage').addEventListener('pointerdown',event=>{if(event.button!==0||!state.canvasSpaceHeld" in js
    assert "window.addEventListener('blur',releaseCanvasSpacePan)" in js
    assert 'var(--canvas-font-size,20.5px) * .585' in css
