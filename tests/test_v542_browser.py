from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v542_proposed_order_toggle_tooltip_and_structural_size_stays_constant(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.2 Proposed Order')
    page, errors = _page(base, scene_browser)
    try:
        setup = page.evaluate("""async d=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""", {'tab': tab['id']})
        page.wait_for_function("()=>document.querySelectorAll('.developmental-structural-tile').length===2")
        assert page.locator('#canvasStructureBtn').is_visible()
        assert page.locator('#canvasStructureBtn').get_attribute('aria-pressed') == 'false'
        assert 'Drag Chapters to reorder' not in page.locator('#developmentalStructureDock').inner_text()

        # The instruction moved into the toolbar tooltip instead of consuming lane space.
        page.locator('#canvasStructureBtn').hover()
        page.wait_for_selector('#canvasStructureTooltip:not(.hidden)', timeout=3000)
        tooltip = page.locator('#canvasStructureTooltip').inner_text()
        assert 'Manage Structure Order' in tooltip
        page.mouse.move(20, 20)

        lane_tile = page.locator('.developmental-structural-tile').filter(has_text='Chapter 2').first
        lane_box = lane_tile.bounding_box()
        assert lane_box
        assert abs(lane_box['width'] - 143) <= 1.5
        assert abs(lane_box['height'] - 52) <= 1.5

        # Structural tiles are screen-stable: the lane ignores Canvas zoom, and an
        # Unplaced structural tile keeps the same physical size on the zoomed desk.
        page.evaluate("()=>applyCanvasZoom(.65,{persist:false})")
        assert page.locator('#canvasZoomValue').inner_text() == '65%'

        # Whole structural tile moves down, but it stays the exact compact tile size.
        stage_box = page.locator('#canvasStage').bounding_box()
        assert stage_box
        page.mouse.move(lane_box['x'] + lane_box['width'] / 2, lane_box['y'] + lane_box['height'] / 2)
        page.mouse.down()
        page.mouse.move(stage_box['x'] + 360, stage_box['y'] + 230, steps=8)
        page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>[...document.querySelectorAll('.canvas-node.developmental-unplaced')].some(el=>el.textContent.includes('Chapter 2'))")
        desk_tile = page.locator('.canvas-node.developmental-unplaced').filter(has_text='Chapter 2').first
        desk_box = desk_tile.bounding_box()
        assert desk_box
        assert abs(desk_box['width'] - lane_box['width']) <= 1.5
        assert abs(desk_box['height'] - lane_box['height']) <= 1.5
        detail = requests.get(base + f'/api/canvases/{setup}', timeout=10).json()
        moved = next(n for n in detail['nodes'] if n['node_type'] == 'chapter' and n['title'] == 'Chapter 2')
        assert (int(moved['w']), int(moved['h'])) == (143, 52)

        # The toolbar icon hides and restores the dock; there is no dead lane-height gap.
        page.locator('#canvasStructureBtn').click();page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        page.locator('#structureOrderToggle').click()
        page.wait_for_function("()=>document.querySelector('#developmentalStructureDock').classList.contains('hidden')")
        assert page.evaluate("()=>getComputedStyle(document.querySelector('.canvas-mode-overlay')).top") == '53px'
        page.locator('#structureOrderToggle').click()
        page.wait_for_function("()=>!document.querySelector('#developmentalStructureDock').classList.contains('hidden')")
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v542_canvas_toolbar_and_panels_follow_manuscript_push_scroll_standard(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.2 Canvas UI Standard')
    page, errors = _page(base, scene_browser)
    try:
        page.set_viewport_size({'width': 760, 'height': 700})
        page.evaluate("""async d=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
        }""", {'tab': tab['id']})
        page.wait_for_selector('#canvasStructureBtn:not(.hidden)')

        selector = '#canvasStructureBtn,#addNodePanelBtn,#addImage'
        before = page.locator(selector).evaluate_all("els=>Object.fromEntries(els.filter(e=>!e.classList.contains('hidden')).map(e=>[e.id,e.getBoundingClientRect().width]))")

        # Use a Developmental-specific panel, not only Add Card, to verify the new panel itself.
        page.locator('#canvasStructureBtn').click()
        page.wait_for_selector('#canvasToolPanel:not(.hidden)')
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle').textContent==='Structure'")
        panel = page.locator('#canvasToolPanel').evaluate("el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el),b=getComputedStyle(document.querySelector('#canvasPanelBody')),list=getComputedStyle(document.querySelector('.canvas-version-list'));return{width:r.width,right:r.right,display:s.display,position:s.position,bodyOverflowY:b.overflowY,listOverflowY:list.overflowY}}")
        assert 289 <= panel['width'] <= 291
        assert panel['right'] <= 761
        assert panel['display'] == 'flex'
        assert panel['position'] == 'static'
        # Structure keeps the standard panel frame while the Version list owns its own scrolling.
        assert panel['listOverflowY'] == 'auto'

        after = page.locator(selector).evaluate_all("els=>Object.fromEntries(els.filter(e=>!e.classList.contains('hidden')).map(e=>[e.id,e.getBoundingClientRect().width]))")
        assert before.keys() == after.keys()
        for key in before:
            assert abs(before[key] - after[key]) <= 0.5, (key, before[key], after[key])

        toolbar = page.locator('.canvas-toolbar')
        metrics = toolbar.evaluate("el=>({clientWidth:el.clientWidth,scrollWidth:el.scrollWidth,overflowX:getComputedStyle(el).overflowX,scrollLeft:el.scrollLeft})")
        assert metrics['scrollWidth'] > metrics['clientWidth']
        assert metrics['overflowX'] == 'auto'
        toolbar.evaluate("el=>el.scrollLeft=el.scrollWidth")
        page.wait_for_function("()=>document.querySelector('.canvas-toolbar').scrollLeft>0")

        # Add Card now follows the compact toolbar-palette pattern instead of
        # opening a second right-side creation panel. The current Structure panel stays put.
        page.locator('#addNodePanelBtn').click()
        page.wait_for_selector('#canvasCardPalette:not(.hidden)')
        assert page.locator('#canvasCardPalette [data-canvas-card-shape]').count() == 2
        assert page.locator('#canvasPanelTitle').inner_text() == 'Structure'
        assert 289 <= page.locator('#canvasToolPanel').evaluate("el=>el.getBoundingClientRect().width") <= 291
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
