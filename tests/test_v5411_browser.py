from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5411_card_palette_draws_directly_and_card_text_contrast_is_automatic(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.15 Card palette')
    page, errors = _page(base, scene_browser)
    try:
        canvas_id = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Drawing',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""")
        page.wait_for_selector('#canvasView.active')

        # Timeline is gone from the writer-facing creation model.
        page.locator('#addCanvas').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('[data-canvas-type]').count() == 2
        assert page.locator('[data-canvas-type="timeline"]').count() == 0
        page.locator('#cancelNewCanvas').click()

        # Add Card opens a compact two-choice palette beneath the toolbar, not a side panel.
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")
        add = page.locator('#addNodePanelBtn')
        add.click()
        palette = page.locator('#canvasCardPalette')
        palette.wait_for(state='visible')
        assert palette.locator('[data-canvas-card-shape]').count() == 2
        ab, pb = add.bounding_box(), palette.bounding_box()
        assert ab and pb and pb['y'] >= ab['y'] + ab['height'] - 1
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")

        # Pick Card, then drag its bounds directly on empty Canvas space.
        palette.locator('[data-canvas-card-shape="rect"]').click()
        assert add.get_attribute('aria-pressed') == 'true'
        assert not palette.is_visible()
        stage = page.locator('#canvasStage')
        box = stage.bounding_box(); assert box
        x1, y1 = box['x'] + 260, box['y'] + 210
        x2, y2 = x1 + 260, y1 + 155
        page.mouse.move(x1, y1); page.mouse.down(); page.mouse.move(x2, y2, steps=8); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.length===1")
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        node = page.evaluate("()=>({id:state.canvasNodes[0].id,shape:state.canvasNodes[0].shape,w:state.canvasNodes[0].w,h:state.canvasNodes[0].h})")
        assert node['shape'] == 'rect'
        assert 250 <= float(node['w']) <= 270
        assert 145 <= float(node['h']) <= 165
        assert add.get_attribute('aria-pressed') == 'false'

        # Direct drawing leaves the workspace unobstructed. A deliberate click on the
        # Card's drag handle opens its inspector, which no longer exposes manual foreground color.
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")
        page.locator(f'.canvas-node[data-id="{node["id"]}"] .node-drag-strip').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Card'")
        assert 'Text color' not in page.locator('#canvasPanelBody').inner_text()

        # Dark and light custom backgrounds drive the same automatic contrast rule as Markers.
        page.locator('#nodeBackgroundChoice').select_option('custom')
        color = page.locator('#nodeBackgroundCustom')
        color.evaluate("el=>{el.value='#101010';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        dark = page.locator(f'.canvas-node[data-id="{node["id"]}"]').evaluate("el=>getComputedStyle(el).color")
        assert dark == 'rgb(236, 236, 234)'
        color.evaluate("el=>{el.value='#f4f4f4';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        light = page.locator(f'.canvas-node[data-id="{node["id"]}"]').evaluate("el=>getComputedStyle(el).color")
        assert light == 'rgb(32, 32, 32)'

        saved = requests.get(base + f'/api/canvases/{canvas_id}', timeout=10).json()
        saved_node = next(n for n in saved['nodes'] if n['id'] == node['id'])
        assert saved_node['shape'] == 'rect'
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
