from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5415_wheel_zooms_and_hidden_card_creation_paths_are_gone(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.15 Canvas input cleanup')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Input cleanup',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');applyCanvasZoom(1);
          await createNodeAt('rect',160,150,{title:'Existing'},{inspect:false});
        }""")
        page.wait_for_selector('#canvasView.active')
        stage = page.locator('#canvasStage')
        box = stage.bounding_box(); assert box

        # A plain mouse wheel gesture zooms around the pointer; Ctrl/Meta is not required.
        page.mouse.move(box['x'] + box['width'] * .62, box['y'] + box['height'] * .55)
        before = page.evaluate("()=>state.canvasZoom")
        page.mouse.wheel(0, -180)
        page.wait_for_function("before=>state.canvasZoom>before", arg=before)
        after = page.evaluate("()=>state.canvasZoom")
        assert after > before

        # Double-clicking empty Canvas space no longer creates a Card.
        before_nodes = page.evaluate("()=>state.canvasNodes.length")
        page.mouse.dblclick(box['x'] + box['width'] * .82, box['y'] + box['height'] * .76)
        page.wait_for_timeout(250)
        assert page.evaluate("()=>state.canvasNodes.length") == before_nodes

        # Background context menu matches the current Card/Figure vocabulary.
        page.mouse.click(box['x'] + box['width'] * .74, box['y'] + box['height'] * .66, button='right')
        menu = page.locator('#contextMenu')
        menu.wait_for(state='visible')
        text = menu.inner_text()
        assert 'Add card here' in text
        assert 'Add text here' in text
        assert 'Add figure' in text
        assert 'oval' not in text.lower()
        assert 'diamond' not in text.lower()

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
