from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v530_canvas_surface_uses_manuscript_ui_language_and_card_terminology():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html
    assert 'id="canvasViewBtn"' in html
    assert html.index('id="canvasViewBtn"') < html.index('id="addNodePanelBtn"')
    assert 'id="openLegend"' not in html
    assert 'id="canvasLegendToggle"' in html
    assert 'id="canvasTextSize"' in html and 'id="canvasFont"' in html
    assert 'id="canvasZoomOut"' in html and 'id="canvasZoomFit"' in html
    assert 'aria-label="Add Card"' in html
    assert 'nav-collapse-button' in html and 'canvas-rail-add' in html
    assert 'Add Node' not in html
    assert 'id="canvasCardPalette"' in html
    assert 'function beginCanvasCardDraw(event,shape)' in js
    assert "openCanvasPanel('Add Card'" not in js
    assert "node:'Canvas Card'" in js
    assert 'Card · ${node.title' in js
    assert '--canvas-font-size' in css and '--canvas-font-family' in css
    assert 'textSize:state.canvasFontSize' in js and 'font:state.canvasFont' in js


def test_v530_view_menu_order_matches_visibility_typography_viewport():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    positions=[html.index(token) for token in (
        'id="canvasLegendToggle"',
        'id="canvasTextSize"',
        'id="canvasFont"',
        'id="canvasZoomOut"',
        'id="canvasZoomFit"',
    )]
    assert positions==sorted(positions)
