from __future__ import annotations

import base64
import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5418_image_context_menu_and_screen_space_tracking(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.18 Canvas image interaction')
    page, errors = _page(base, scene_browser)
    try:
        tiny = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n7sAAAAASUVORK5CYII=')
        uploaded = requests.post(base + '/api/assets', headers={'X-Breadmaker-Client': 'v41812-browser'}, files={'file': ('initial.png', tiny, 'image/png')}, timeout=5)
        assert uploaded.ok, uploaded.text
        asset = uploaded.json()['path']
        node_id = page.evaluate("""async asset=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Images',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          applyCanvasZoom(2.2,{persist:false});
          const node=await createNodeAt('rect',180,150,{node_type:'image',image_path:asset,w:180,h:120,color:''},{inspect:false,label:'Test image'});
          // Pointer math must follow the actual DOM transform, not a stale state value.
          state.canvasZoom=1;
          return node.id;
        }""", asset)
        page.wait_for_selector(f'.canvas-node.image-object[data-id="{node_id}"]')
        image = page.locator(f'.canvas-node.image-object[data-id="{node_id}"]')
        assert page.locator('#canvasToolPanel').evaluate('(el)=>el.classList.contains("hidden")')

        before = image.bounding_box(); assert before
        page.mouse.move(before['x'] + 30, before['y'] + 30)
        page.mouse.down()
        page.mouse.move(before['x'] + 150, before['y'] + 90, steps=5)
        page.mouse.up()
        page.wait_for_timeout(100)
        after = image.bounding_box(); assert after
        assert abs((after['x'] - before['x']) - 120) < 5
        assert abs((after['y'] - before['y']) - 60) < 5

        # The lasso should visually track the pointer one-for-one in screen space too.
        stage = page.locator('#canvasStage')
        sb = stage.bounding_box(); assert sb
        sx, sy = sb['x'] + 80, sb['y'] + 80
        page.mouse.move(sx, sy)
        page.mouse.down()
        page.mouse.move(sx + 140, sy + 90, steps=3)
        selection = page.locator('#selectionRect')
        box = selection.bounding_box(); assert box
        assert abs(box['width'] - 140) < 5
        assert abs(box['height'] - 90) < 5
        page.mouse.up()

        moved = image.bounding_box(); assert moved
        page.mouse.click(moved['x'] + 25, moved['y'] + 25, button='right')
        menu = page.locator('#contextMenu')
        menu.wait_for(state='visible')
        text = menu.inner_text()
        assert 'Replace Image…' in text
        assert 'Delete Image' in text
        assert page.locator('#canvasToolPanel').evaluate('(el)=>el.classList.contains("hidden")')

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
