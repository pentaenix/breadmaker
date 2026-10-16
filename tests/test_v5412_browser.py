from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5412_drawing_is_smooth_switches_to_pointer_drag_is_immediate_and_empty_connector_is_noop(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.15 Canvas interaction cleanup')
    page, errors = _page(base, scene_browser)
    try:
        canvas_id = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Interaction cleanup',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('hand');return cv.id;
        }""")
        page.wait_for_selector('#canvasView.active')
        assert page.locator('#canvasHandMode').get_attribute('aria-pressed') == 'true'

        # Arm Card drawing. Pointer-down alone must not flash a full default-sized preview.
        page.locator('#addNodePanelBtn').click()
        page.locator('[data-canvas-card-shape="rect"]').click()
        stage = page.locator('#canvasStage')
        box = stage.bounding_box(); assert box
        x1, y1 = box['x'] + 250, box['y'] + 220
        page.mouse.move(x1, y1); page.mouse.down()
        preview = page.locator('.canvas-card-draw-preview')
        assert preview.count() == 1
        assert preview.evaluate("el=>el.classList.contains('hidden')")

        # Tiny motion stays dormant. Once the drag starts, preview grows from the actual bounds,
        # rather than snapping from the shape's default size.
        page.mouse.move(x1 + 2, y1 + 2)
        assert preview.evaluate("el=>el.classList.contains('hidden')")
        page.mouse.move(x1 + 30, y1 + 22)
        assert not preview.evaluate("el=>el.classList.contains('hidden')")
        pb = preview.bounding_box(); assert pb
        assert 20 <= pb['width'] <= 40
        assert 14 <= pb['height'] <= 32

        page.mouse.move(x1 + 230, y1 + 120, steps=6); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.length===1")
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        created = page.evaluate("()=>({id:state.canvasNodes[0].id,x:state.canvasNodes[0].x,y:state.canvasNodes[0].y,shape:state.canvasNodes[0].shape})")
        assert created['shape'] == 'rect'

        # Creation is one-shot and hands control to Pointer immediately; it does not force title editing.
        assert page.locator('#canvasPointerMode').get_attribute('aria-pressed') == 'true'
        assert page.evaluate("()=>state.canvasMode") == 'pointer'
        assert page.locator('#addNodePanelBtn').get_attribute('aria-pressed') == 'false'
        assert not page.evaluate("()=>document.activeElement?.classList?.contains('node-title')")

        # The newly-created Card can be dragged immediately without another mode switch/click cycle.
        node = page.locator(f'.canvas-node[data-id="{created["id"]}"]')
        handle = node.locator('.node-drag-strip')
        nb, hb = node.bounding_box(), handle.bounding_box(); assert nb and hb
        assert hb['width'] > 80
        hx, hy = hb['x'] + hb['width']/2, hb['y'] + hb['height']/2
        page.mouse.move(hx, hy); page.mouse.down(); page.mouse.move(hx + 65, hy + 38, steps=5); page.mouse.up()
        page.wait_for_function("([id,x,y])=>{const n=state.canvasNodes.find(n=>n.id===id);return n&&n.x>x+45&&n.y>y+20}", arg=[created['id'], created['x'], created['y']])
        page.wait_for_timeout(80)
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")

        # A click without a drag still opens the inspector intentionally.
        handle.click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Card'")
        page.locator('[data-close-canvas-panel]').click()

        # Text-only Cards retain the compact centered handle language. Deprecated shape Cards are no longer creatable.
        other_ids = page.evaluate("""async()=>{
          const b=await createNodeAt('text',650,420,{w:190,h:60});
          return [b.id];
        }""")
        for node_id in other_ids:
            item = page.locator(f'.canvas-node[data-id="{node_id}"]')
            grip = item.locator('.node-drag-strip')
            ib, gb = item.bounding_box(), grip.bounding_box(); assert ib and gb
            assert 26 <= gb['width'] <= 34 and 4 <= gb['height'] <= 9
            assert abs((gb['x'] + gb['width']/2) - (ib['x'] + ib['width']/2)) <= 3

        # Dragging a connector to empty Canvas no longer creates a surprise Card or edge.
        counts = page.evaluate("()=>({nodes:state.canvasNodes.length,edges:state.canvasEdges.length})")
        node.hover()
        port = node.locator('.node-port.right'); port_box = port.bounding_box(); assert port_box
        sx, sy = port_box['x'] + port_box['width']/2, port_box['y'] + port_box['height']/2
        empty_x, empty_y = box['x'] + box['width'] - 120, box['y'] + box['height'] - 110
        page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(empty_x, empty_y, steps=6); page.mouse.up()
        page.wait_for_timeout(120)
        after = page.evaluate("()=>({nodes:state.canvasNodes.length,edges:state.canvasEdges.length})")
        assert after == counts

        saved = requests.get(base + f'/api/canvases/{canvas_id}', timeout=10).json()
        assert len(saved['nodes']) == counts['nodes']
        assert len(saved['edges']) == counts['edges']
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
