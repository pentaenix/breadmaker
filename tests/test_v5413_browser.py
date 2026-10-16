from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5413_background_figures_and_full_containment_marquee(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.15 Figures and containment selection')
    page, errors = _page(base, scene_browser)
    try:
        canvas_id = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Figure canvas',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');return cv.id;
        }""")
        page.wait_for_selector('#canvasView.active')

        # Card creation is now only information Card / Text-only.
        page.locator('#addNodePanelBtn').click()
        page.wait_for_selector('#canvasCardPalette:not(.hidden)')
        assert page.locator('#canvasCardPalette [data-canvas-card-shape]').count() == 2
        assert page.locator('#canvasCardPalette [data-canvas-card-shape="oval"]').count() == 0
        assert page.locator('#canvasCardPalette [data-canvas-card-shape="diamond"]').count() == 0
        page.keyboard.press('Escape')

        # Add Figure uses its separate figure palette under the toolbar.
        add = page.locator('#addFigureBtn')
        add.click()
        palette = page.locator('#canvasFigurePalette')
        palette.wait_for(state='visible')
        assert palette.locator('[data-canvas-figure-shape]').count() == 5
        ab, pb = add.bounding_box(), palette.bounding_box()
        assert ab and pb and pb['y'] >= ab['y'] + ab['height'] - 1

        # Draw a rectangle figure. It has no text/content and lives in the dedicated back layer.
        palette.locator('[data-canvas-figure-shape="rect"]').click()
        stage = page.locator('#canvasStage')
        box = stage.bounding_box(); assert box
        x1, y1 = box['x'] + 120, box['y'] + 120
        x2, y2 = x1 + 320, y1 + 210
        page.mouse.move(x1, y1); page.mouse.down(); page.mouse.move(x2, y2, steps=7); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.some(n=>n.node_type==='figure')")
        rect = page.evaluate("()=>{const n=state.canvasNodes.find(n=>n.node_type==='figure');return {id:n.id,x:n.x,y:n.y,w:n.w,h:n.h,shape:n.shape,title:n.title,body:n.body}}")
        assert rect['shape'] == 'rect' and rect['title'] == '' and rect['body'] == ''
        fig = page.locator(f'#figures .canvas-figure[data-id="{rect["id"]}"]')
        assert fig.count() == 1
        style = fig.evaluate("el=>({bg:getComputedStyle(el).backgroundColor,borderWidth:getComputedStyle(el).borderTopWidth,stroke:getComputedStyle(el.querySelector('.figure-shape')).stroke,text:el.textContent.trim()})")
        assert style['bg'] == 'rgba(0, 0, 0, 0)'
        assert style['borderWidth'] == '0px'
        assert style['stroke']
        assert style['text'] == ''

        # A Card overlapping the figure is above it, proving Figure is a background organizer rather than a container.
        card_id = page.evaluate("""async()=>{const n=await createNodeAt('rect',180,165,{w:150,h:90,title:'Inside idea',body:''},{inspect:false});return n.id}""")
        page.wait_for_selector(f'#nodes .canvas-node[data-id="{card_id}"]')
        top_id = page.evaluate("""([stageX,stageY])=>{
          const el=document.elementFromPoint(stageX+230,stageY+200);return +(el?.closest('.canvas-node')?.dataset.id||0)
        }""", [box['x'], box['y']])
        assert top_id == card_id

        # Figures drag from anywhere: grab the rectangle interior, not a special handle.
        before = page.evaluate("id=>{const n=state.canvasNodes.find(n=>n.id===id);return {x:n.x,y:n.y}}", rect['id'])
        card_before = page.evaluate("id=>{const n=state.canvasNodes.find(n=>n.id===id);return {x:n.x,y:n.y}}", card_id)
        fb = fig.bounding_box(); assert fb
        cx, cy = fb['x'] + 35, fb['y'] + 35
        page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 55, cy + 40, steps=5); page.mouse.up()
        page.wait_for_function("([id,x,y])=>{const n=state.canvasNodes.find(n=>n.id===id);return n&&n.x>x+40&&n.y>y+25}", arg=[rect['id'], before['x'], before['y']])
        card_after = page.evaluate("id=>{const n=state.canvasNodes.find(n=>n.id===id);return {x:n.x,y:n.y}}", card_id)
        assert card_after == card_before

        # Circle drawing stays circular even when the pointer travels through a rectangular drag path.
        add.click(); palette.locator('[data-canvas-figure-shape="circle"]').click()
        sx, sy = box['x'] + 600, box['y'] + 130
        page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 120, sy + 190, steps=6); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.filter(n=>n.node_type==='figure').length===2")
        circle = page.evaluate("()=>{const n=state.canvasNodes.filter(n=>n.node_type==='figure').find(n=>n.shape==='circle');return {id:n.id,w:n.w,h:n.h}}")
        assert abs(float(circle['w']) - float(circle['h'])) <= 0.01

        # Draw.io-style marquee: a fully contained object is selected; a merely touched object is not.
        ids = page.evaluate("""async()=>{
          const inside=await createNodeAt('rect',900,500,{w:100,h:70,title:'Fully inside'},{inspect:false});
          const partial=await createNodeAt('rect',1060,510,{w:120,h:70,title:'Only touched'},{inspect:false});
          return [inside.id,partial.id]
        }""")
        page.evaluate("()=>{state.selectedNodes.clear();renderNodeSelection();closeCanvasPanel();setCanvasMode('pointer')}")
        # marquee world bounds ~880,480 -> 1110,650: inside is fully contained, partial extends beyond right edge.
        start_x, start_y = box['x'] + 880, box['y'] + 480
        end_x, end_y = box['x'] + 1110, box['y'] + 650
        # Scroll so that the test area is visible, then recompute client coordinates from world positions.
        page.evaluate("()=>{const s=document.querySelector('#canvasStage');s.scrollLeft=820;s.scrollTop=430}")
        page.wait_for_timeout(50)
        scroll = page.evaluate("()=>({x:document.querySelector('#canvasStage').scrollLeft,y:document.querySelector('#canvasStage').scrollTop})")
        start_x, start_y = box['x'] + 880 - scroll['x'], box['y'] + 480 - scroll['y']
        end_x, end_y = box['x'] + 1110 - scroll['x'], box['y'] + 650 - scroll['y']
        print(page.evaluate("""([sx,sy,ex,ey,ids])=>({zoom:state.canvasZoom,scroll:{x:document.querySelector('#canvasStage').scrollLeft,y:document.querySelector('#canvasStage').scrollTop},start:canvasPointFromClient(sx,sy),end:canvasPointFromClient(ex,ey),nodes:ids.map(id=>{const n=state.canvasNodes.find(n=>n.id===id);return {...n,size:renderedNodeSize(n)}})})""", [start_x,start_y,end_x,end_y,ids]))
        page.mouse.move(start_x, start_y); page.mouse.down(); page.mouse.move(end_x, end_y, steps=6); page.mouse.up()
        selected = page.evaluate("()=>[...state.selectedNodes]")
        assert ids[0] in selected
        assert ids[1] not in selected

        saved = requests.get(base + f'/api/canvases/{canvas_id}', timeout=10).json()
        figures = [n for n in saved['nodes'] if n['node_type'] == 'figure']
        assert len(figures) == 2
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
