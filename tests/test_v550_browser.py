from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v550_canvas_rail_description_and_overflow_match_context_menu(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.5 Canvas rail')
    page, errors = _page(base, scene_browser)
    try:
        cid = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Plot Board',description:'Keep the middle act visually balanced.',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""")
        row = page.locator('.canvas-item').filter(has_text='Plot Board').first
        row.hover()
        page.wait_for_selector('#canvasDetailsHover', timeout=2500)
        flyer = page.locator('#canvasDetailsHover')
        assert 'Keep the middle act visually balanced.' in flyer.inner_text()
        assert 'chapter-details-hover-card' in (flyer.get_attribute('class') or '')

        more = row.locator('.canvas-item-more')
        more.click()
        page.wait_for_selector('#contextMenu:not(.hidden)')
        overflow_labels = [x.strip() for x in page.locator('#contextMenu button').all_text_contents()]
        page.keyboard.press('Escape')
        row.click(button='right')
        page.wait_for_selector('#contextMenu:not(.hidden)')
        context_labels = [x.strip() for x in page.locator('#contextMenu button').all_text_contents()]
        assert overflow_labels == context_labels
        assert 'New canvas inside…' in context_labels
        assert 'Configure…' in context_labels
        assert 'Delete canvas…' in context_labels
        assert cid == page.evaluate('()=>state.currentCanvas.id')
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v550_snap_toggle_alignment_and_space_pan_over_objects(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.5 Snap and Space')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Snap',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const a=await createNodeAt('rect',200,300,{node_type:'figure',w:100,h:100,title:'',body:'',color:''},{inspect:false,label:'A'});
          const b=await createNodeAt('rect',500,304,{node_type:'figure',w:100,h:100,title:'',body:'',color:''},{inspect:false,label:'B'});
          return {a:a.id,b:b.id};
        }""")
        page.locator('#canvasViewBtn').click()
        snap = page.locator('#canvasSnapToggle')
        assert snap.get_attribute('aria-checked') == 'true'
        snap.click()
        assert snap.get_attribute('aria-checked') == 'false'
        snap.click()
        assert snap.get_attribute('aria-checked') == 'true'
        page.keyboard.press('Escape')

        moving = page.locator(f'.canvas-node.canvas-figure[data-id="{ids["b"]}"]')
        box = moving.bounding_box(); assert box
        # Aim close to an 18px gap and horizontal alignment; Snap should settle both axes.
        page.mouse.move(box['x'] + 50, box['y'] + 50)
        page.mouse.down()
        page.mouse.move(box['x'] + 50 - 179, box['y'] + 50 - 2, steps=5)
        assert page.locator('#canvasGuides .canvas-snap-guide').count() >= 1
        page.mouse.up()
        page.wait_for_timeout(100)
        coords = page.evaluate("id=>{const n=state.canvasNodes.find(x=>x.id===id);return{x:n.x,y:n.y}}", ids['b'])
        assert abs(coords['x'] - 318) < 0.6
        assert abs(coords['y'] - 300) < 0.6
        assert page.locator('#canvasGuides .canvas-snap-guide').count() == 0

        # Space must pan even when the gesture begins on top of a Canvas object.
        page.evaluate("()=>{const s=$('#canvasStage');s.scrollLeft=350;s.scrollTop=220}")
        before = page.evaluate("id=>{const n=state.canvasNodes.find(x=>x.id===id);return{x:n.x,y:n.y,left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop}}", ids['b'])
        box = moving.bounding_box(); assert box
        page.keyboard.down('Space')
        assert page.evaluate('()=>state.canvasSpaceHeld') is True
        page.mouse.move(box['x'] + 50, box['y'] + 50)
        page.mouse.down()
        page.mouse.move(box['x'] - 50, box['y'] + 10, steps=4)
        page.mouse.up()
        page.keyboard.up('Space')
        after = page.evaluate("id=>{const n=state.canvasNodes.find(x=>x.id===id);return{x:n.x,y:n.y,left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop,space:state.canvasSpaceHeld}}", ids['b'])
        assert after['x'] == before['x'] and after['y'] == before['y']
        assert after['left'] > before['left'] + 70
        assert after['space'] is False
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v550_structure_tiles_follow_canvas_text_size(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.5 Structure typography')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async d=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
        }""", {'tab': tab['id']})
        tile = page.locator('.developmental-structural-tile .developmental-tile-title').first
        before = float(tile.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        page.evaluate("()=>applyCanvasTextSize(28,{persist:false})")
        after = float(tile.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        assert after > before + 3
        page.evaluate("()=>applyCanvasTextSize(11,{persist:false})")
        small = float(tile.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        assert small < before
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
