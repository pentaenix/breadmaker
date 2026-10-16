from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def _center(box):
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def test_v544_structure_order_drag_has_live_source_and_target_spaces(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.4 Structure Order Drag')
    page, errors = _page(base, scene_browser)
    try:
        canvas_id = page.evaluate("""async d=>{
          for(const title of ['Two','Three','Four']) await api('/api/chapters',{method:'POST',body:JSON.stringify({title,tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""", {'tab': tab['id']})
        page.wait_for_function("()=>document.querySelectorAll('.developmental-structural-tile').length===4")
        assert page.locator('#developmentalStructureDock .developmental-structure-head b').inner_text().title() == 'Structure Order (Chapter)'
        assert 'Proposed Order' not in page.locator('#developmentalStructureDock').inner_text()

        two = page.locator('.developmental-structural-tile').filter(has_text='Two').first
        four = page.locator('.developmental-structural-tile').filter(has_text='Four').first
        two_box = two.bounding_box(); four_before = four.bounding_box()
        assert two_box and four_before
        sx, sy = _center(two_box)

        # Lift Two and hover immediately before Four. The original slot remains
        # physically reserved while a second full-width target opens before Four.
        page.mouse.move(sx, sy); page.mouse.down()
        page.mouse.move(sx + 8, sy + 2, steps=2)
        page.mouse.move(four_before['x'] + 8, four_before['y'] + four_before['height']/2, steps=8)
        page.wait_for_selector('.developmental-drag-proxy')
        page.wait_for_selector('.developmental-drag-source', state='attached')
        page.wait_for_selector('.developmental-lane-drop-gap')
        page.wait_for_timeout(190)

        source = page.locator('.developmental-drag-source')
        source_metrics = source.evaluate("el=>{const r=el.getBoundingClientRect();return{w:r.width,h:r.height,visibility:getComputedStyle(el).visibility}}")
        assert abs(source_metrics['w'] - 143) <= 1.5
        assert abs(source_metrics['h'] - 52) <= 1.5
        assert source_metrics['visibility'] == 'hidden'
        proxy_box = page.locator('.developmental-drag-proxy').bounding_box()
        gap_box = page.locator('.developmental-lane-drop-gap').bounding_box()
        four_during = page.locator('.developmental-structural-tile').filter(has_text='Four').bounding_box()
        assert proxy_box and gap_box and four_during
        assert abs(proxy_box['width'] - 143) <= 2
        assert abs(proxy_box['height'] - 52) <= 2
        assert 141 <= gap_box['width'] <= 145
        assert four_during['x'] >= four_before['x'] + 135

        page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>!document.querySelector('.developmental-drag-proxy')&&!document.querySelector('.developmental-lane-drop-gap')")
        lane_titles = page.locator('.developmental-structural-tile .developmental-tile-title').all_inner_texts()
        assert lane_titles[-3:] == ['Three', 'Two', 'Four']

        # Move the compact tile to a zoomed Canvas. Its 52px content row remains
        # physically and vertically centered rather than inheriting note-card layout.
        page.evaluate("()=>applyCanvasZoom(.65,{persist:false})")
        two = page.locator('.developmental-structural-tile').filter(has_text='Two').first
        two_box = two.bounding_box(); stage_box = page.locator('#canvasStage').bounding_box()
        assert two_box and stage_box
        page.mouse.move(*_center(two_box)); page.mouse.down()
        page.mouse.move(stage_box['x'] + 350, stage_box['y'] + 235, steps=8); page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>[...document.querySelectorAll('.canvas-node.developmental-unplaced')].some(el=>el.textContent.includes('Two'))")
        desk = page.locator('.canvas-node.developmental-unplaced').filter(has_text='Two').first
        metrics = desk.evaluate("el=>{const r=el.getBoundingClientRect(),inner=el.querySelector('.developmental-desk-tile').getBoundingClientRect(),title=el.querySelector('.developmental-tile-title').getBoundingClientRect();return{w:r.width,h:r.height,center:r.top+r.height/2,innerCenter:inner.top+inner.height/2,titleCenter:title.top+title.height/2,innerH:inner.height}}")
        assert abs(metrics['w'] - 143) <= 1.5 and abs(metrics['h'] - 52) <= 1.5
        assert metrics['innerH'] >= 49
        assert abs(metrics['center'] - metrics['innerCenter']) <= 1
        assert abs(metrics['center'] - metrics['titleCenter']) <= 2

        # Returning the Unplaced tile uses the same full destination-cell preview.
        first = page.locator('.developmental-structural-tile').first
        first_box = first.bounding_box(); desk_box = desk.bounding_box()
        assert first_box and desk_box
        page.mouse.move(*_center(desk_box)); page.mouse.down()
        page.mouse.move(first_box['x'] + first_box['width'] - 6, first_box['y'] + first_box['height']/2, steps=8)
        page.wait_for_selector('.developmental-drag-proxy')
        page.wait_for_selector('.developmental-lane-drop-gap')
        page.wait_for_timeout(190)
        return_gap = page.locator('.developmental-lane-drop-gap').bounding_box()
        assert return_gap and 141 <= return_gap['width'] <= 145
        page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>!document.querySelector('.developmental-drag-proxy')&&!document.querySelector('.developmental-lane-drop-gap')")

        detail = requests.get(base + f'/api/canvases/{canvas_id}', timeout=10).json()
        two_node = next(n for n in detail['nodes'] if n['title'] == 'Two')
        assert two_node['lane_position'] is not None
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
