from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _open_structure(page):
    if page.locator('#canvasPanelTitle').inner_text() != 'Structure' or page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')"):
        page.locator('#canvasStructureBtn').click()
    page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'&&!document.querySelector('#canvasToolPanel').classList.contains('hidden')")


def test_v541_large_developmental_lane_scrolls_without_changing_canvas_zoom(scene_live_server,scene_browser):
    base=scene_live_server;tab,ch1=_new_project(base,'5.4.1 Large Lane');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async d=>{for(let i=2;i<=60;i++)await api('/api/chapters',{method:'POST',body:JSON.stringify({title:`Chapter ${i}`,tab_id:d.tab})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Large Development',canvas_type:'developmental',developmental_scope:'chapters'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id}""",{'tab':tab['id']})
        page.wait_for_selector('#developmentalStructureDock:not(.hidden)')
        page.wait_for_function("()=>document.querySelectorAll('.developmental-structural-tile').length===60")
        assert page.locator('.developmental-structural-tile').count()==60
        assert page.locator('.canvas-node.developmental-placed').count()==0
        assert page.evaluate("()=>state.canvasZoom")==1
        assert page.locator('#canvasZoomValue').inner_text()=='100%'
        metrics=page.locator('#developmentalLaneViewport').evaluate("el=>({clientWidth:el.clientWidth,scrollWidth:el.scrollWidth,scrollLeft:el.scrollLeft})")
        assert metrics['scrollWidth']>metrics['clientWidth']*5
        stage_before=page.locator('#canvasStage').evaluate("el=>({left:el.scrollLeft,top:el.scrollTop})")
        page.locator('#developmentalLaneViewport').evaluate("el=>el.scrollLeft=el.scrollWidth")
        page.wait_for_function("()=>document.querySelector('#developmentalLaneViewport').scrollLeft>1000")
        assert page.evaluate("()=>state.canvasZoom")==1
        stage_after=page.locator('#canvasStage').evaluate("el=>({left:el.scrollLeft,top:el.scrollTop})")
        assert stage_after==stage_before
        detail=requests.get(base+f'/api/canvases/{setup}',timeout=10).json()
        structural=sorted([n for n in detail['nodes'] if n['node_type']=='chapter'],key=lambda n:n['lane_position'])
        assert [n['lane_position'] for n in structural]==list(range(60))
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v541_structural_tiles_drag_whole_and_planning_card_promotes(scene_live_server,scene_browser):
    base=scene_live_server;tab,ch1=_new_project(base,'5.4.1 Planning Lane');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async d=>{const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});const ch3=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:d.tab})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return{canvas:cv.id,ch2:ch2.id,ch3:ch3.id}}""",{'tab':tab['id']})
        page.wait_for_function("()=>document.querySelectorAll('.developmental-structural-tile').length===3")
        # Structural tiles have no drag strip: the whole tile moves to the Canvas.
        tile=page.locator('.developmental-structural-tile').filter(has_text='Chapter 2').first
        assert tile.locator('.node-drag-strip').count()==0
        tb=tile.bounding_box();sb=page.locator('#canvasStage').bounding_box();assert tb and sb
        page.mouse.move(tb['x']+tb['width']/2,tb['y']+tb['height']/2);page.mouse.down();page.mouse.move(sb['x']+430,sb['y']+260,steps=8);page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>[...document.querySelectorAll('.canvas-node.developmental-unplaced')].some(el=>el.textContent.includes('Chapter 2'))")
        _open_structure(page);assert page.locator('#reviewStructureChanges').is_disabled();page.locator('#canvasStructureBtn').click()
        # The compact desk tile is also draggable by its whole surface back into the lane.
        desk=page.locator('.canvas-node.developmental-unplaced').filter(has_text='Chapter 2').first
        db=desk.bounding_box();first=page.locator('.developmental-structural-tile').first.bounding_box();assert db and first
        page.mouse.move(db['x']+db['width']/2,db['y']+db['height']/2);page.mouse.down();page.mouse.move(first['x']+first['width']-8,first['y']+first['height']/2,steps=8);page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>document.querySelector('.canvas-node.developmental-unplaced')===null")
        _open_structure(page);assert not page.locator('#reviewStructureChanges').is_disabled();page.locator('#canvasStructureBtn').click()
        # Ordinary Card stays a normal Card on the desk and enters the lane only by its normal drag strip.
        idea=page.evaluate("""async d=>{const n=await api(`/api/canvases/${d.canvas}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:330,y:320,title:'The Ambush',body:'Maybe this belongs here'})});await selectCanvas(d.canvas);return n.id}""",setup)
        card=page.locator(f'.canvas-node[data-id="{idea}"]');page.wait_for_selector(f'.canvas-node[data-id="{idea}"]')
        strip=card.locator('.node-drag-strip');assert strip.count()==1
        cb=strip.bounding_box();target=page.locator('.developmental-structural-tile').filter(has_text='Chapter 2').first.bounding_box();assert cb and target
        page.mouse.move(cb['x']+cb['width']/2,cb['y']+4);page.mouse.down();page.mouse.move(target['x']+5,target['y']+target['height']/2,steps=8);page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_selector('.developmental-planning-tile')
        planning=page.locator('.developmental-planning-tile').filter(has_text='The Ambush').first
        assert planning.is_visible()
        _open_structure(page);assert not page.locator('#reviewStructureChanges').is_disabled();page.locator('#canvasStructureBtn').click()
        # Promotion is the explicit manuscript mutation. It reuses the same Canvas node.
        planning.click(button='right');page.locator('#contextMenu button',has_text='Create Chapter in Manuscript').click()
        page.wait_for_selector('#dialogConfirm');page.locator('#dialogConfirm').click()
        page.wait_for_function("d=>[...document.querySelectorAll('.developmental-structural-tile')].some(el=>+el.dataset.nodeId===d)",arg=idea)
        assert page.locator('.developmental-planning-tile').filter(has_text='The Ambush').count()==0
        manuscript=[c for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit')]
        assert len(manuscript)==4
        assert [c['title'] for c in manuscript].count('The Ambush')==1
        detail=requests.get(base+f"/api/canvases/{setup['canvas']}",timeout=10).json();promoted=next(n for n in detail['nodes'] if n['id']==idea)
        assert promoted['node_type']=='chapter' and promoted['chapter_id']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
