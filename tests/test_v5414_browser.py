from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5414_drawing_is_exclusive_and_cards_paste_at_mouse(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.15 Exclusive draw and mouse paste')
    page, errors = _page(base, scene_browser)
    try:
        canvas_id = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Copy canvas',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');return cv.id;
        }""")
        page.wait_for_selector('#canvasView.active')
        stage = page.locator('#canvasStage')
        box = stage.bounding_box(); assert box

        ids = page.evaluate("""async()=>{
          const a=await createNodeAt('rect',180,170,{w:180,h:100,title:'Alpha',body:'First',color:'#222222'},{inspect:false});
          const b=await createNodeAt('text',430,205,{w:160,h:70,title:'Beta',body:'Second',icon:'★'},{inspect:false});
          const f=await createNodeAt('rect',130,125,{node_type:'figure',w:520,h:260,title:'',body:'',color:'',text_color:'',icon:''},{inspect:false,label:'Create Canvas Figure'});
          const e=await api(`/api/canvases/${state.currentCanvas.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left',label:'internal'})});
          e.developmental_hidden=0;state.canvasEdges.push(e);renderNodes();drawEdges();return {a:a.id,b:b.id,f:f.id,e:e.id};
        }""")

        original = page.evaluate("ids=>Object.fromEntries([ids.a,ids.b,ids.f].map(id=>{const n=state.canvasNodes.find(x=>x.id===id);return [id,{x:n.x,y:n.y}]}))", ids)

        # Arm Card drawing. Existing objects remain visible but become pointer-inert.
        page.locator('#addNodePanelBtn').click()
        page.locator('#canvasCardPalette [data-canvas-card-shape="rect"]').click()
        assert page.locator('#canvasStage').evaluate("el=>el.classList.contains('card-create-mode')")
        inert = page.evaluate("ids=>({card:getComputedStyle(document.querySelector(`.canvas-node[data-id='${ids.a}']`)).pointerEvents,figure:getComputedStyle(document.querySelector(`#figures .canvas-node[data-id='${ids.f}']`)).pointerEvents,edges:getComputedStyle(document.querySelector('#edgeLayer')).pointerEvents})", ids)
        assert inert == {'card': 'none', 'figure': 'none', 'edges': 'none'}

        # Drag across the existing Card/Figure. The old objects cannot move; the gesture creates a new Card instead.
        sx, sy = box['x'] + 210, box['y'] + 190
        page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 135, sy + 90, steps=5); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.filter(n=>n.node_type==='free').length===3")
        after_draw = page.evaluate("ids=>Object.fromEntries([ids.a,ids.b,ids.f].map(id=>{const n=state.canvasNodes.find(x=>x.id===id);return [id,{x:n.x,y:n.y}]}))", ids)
        assert after_draw == original
        assert page.locator('#canvasStage').evaluate("el=>!el.classList.contains('card-create-mode')")

        # Copy the two original Cards as a selection. The surrounding Figure is selected too but must not be copied.
        page.evaluate("ids=>{state.selectedNodes=new Set([ids.a,ids.b,ids.f]);state.selectedEdgeId=null;renderNodeSelection();closeCanvasPanel()}", ids)
        before_counts = page.evaluate("()=>({nodes:state.canvasNodes.length,free:state.canvasNodes.filter(n=>n.node_type==='free').length,figures:state.canvasNodes.filter(n=>n.node_type==='figure').length,edges:state.canvasEdges.length})")
        page.keyboard.press('Control+c')
        page.wait_for_function("()=>state.canvasClipboard?.nodes?.length===2")
        clip = page.evaluate("()=>({nodes:state.canvasClipboard.nodes.length,edges:state.canvasClipboard.edges.length})")
        assert clip == {'nodes': 2, 'edges': 1}

        # Move the pointer to the intended paste position. The pasted group's center should land there.
        target_client_x, target_client_y = box['x'] + 820, box['y'] + 520
        page.mouse.move(target_client_x, target_client_y)
        target_world = page.evaluate("([x,y])=>canvasPointFromClient(x,y)", [target_client_x, target_client_y])
        page.keyboard.press('Control+v')
        page.wait_for_function("before=>state.canvasNodes.length===before.nodes+2&&state.canvasEdges.length===before.edges+1", arg=before_counts)

        result = page.evaluate("""before=>{
          const pasted=state.canvasNodes.filter(n=>state.selectedNodes.has(n.id));
          const left=Math.min(...pasted.map(n=>n.x)),top=Math.min(...pasted.map(n=>n.y));
          const right=Math.max(...pasted.map(n=>n.x+renderedNodeSize(n).w)),bottom=Math.max(...pasted.map(n=>n.y+renderedNodeSize(n).h));
          return {count:pasted.length,titles:pasted.map(n=>n.title).sort(),center:{x:(left+right)/2,y:(top+bottom)/2},figures:state.canvasNodes.filter(n=>n.node_type==='figure').length,edges:state.canvasEdges.length,free:state.canvasNodes.filter(n=>n.node_type==='free').length};
        }""", before_counts)
        assert result['count'] == 2
        assert result['titles'] == ['Alpha', 'Beta']
        assert result['figures'] == before_counts['figures']
        assert result['free'] == before_counts['free'] + 2
        assert result['edges'] == before_counts['edges'] + 1
        assert abs(result['center']['x'] - target_world['x']) <= 2.5
        assert abs(result['center']['y'] - target_world['y']) <= 2.5

        saved = requests.get(base + f'/api/canvases/{canvas_id}', timeout=10).json()
        assert len([n for n in saved['nodes'] if n['node_type'] == 'figure']) == 1
        assert len([n for n in saved['nodes'] if n['node_type'] == 'free']) == before_counts['free'] + 2
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
