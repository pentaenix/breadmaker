from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v560_routing_and_connector_ui_are_direct_and_compact(scene_live_server, scene_browser):
    base=scene_live_server
    _new_project(base,'5.6 Routing and Connectors')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Routing',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const a=await createNodeAt('rect',180,180,{node_type:'free',w:220,h:120,title:'Source',body:'Body A'},{inspect:false});
          const r=await createRoutingPoint(520,250);
          const b=await createNodeAt('rect',760,180,{node_type:'free',w:220,h:120,title:'Target',body:'Body B'},{inspect:false});
          const e1=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:r.id,from_port:'right',to_port:'center',label:'Via point'})});
          const e2=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:r.id,to_node_id:b.id,from_port:'center',to_port:'left'})});
          state.canvasEdges.push(e1,e2);renderNodes();drawEdges();return{a:a.id,r:r.id,b:b.id,e1:e1.id,e2:e2.id};
        }""")
        routing=page.locator(f'.canvas-node.routing-node[data-id="{ids["r"]}"]')
        assert routing.count()==1
        assert routing.locator('.routing-output').count()==1
        rb=routing.bounding_box(); assert rb and rb['width'] <= 14 and rb['height'] <= 14

        page.evaluate('id=>selectEdge(id,true)',ids['e1'])
        page.wait_for_selector('#canvasToolPanel:not(.hidden)')
        assert page.locator('#canvasPanelTitle').inner_text()=='Connector'
        assert page.locator('#edgeFromNode').count()==0
        assert page.locator('#edgeToNode').count()==0
        assert page.locator('[data-edge-choice="route_style"]').count()==2
        assert page.locator('[data-edge-choice="line_style"]').count()==3
        page.locator('[data-edge-choice="route_style"][data-edge-value="straight"]').click()
        page.locator('[data-edge-choice="line_width"][data-edge-value="4"]').click()
        page.wait_for_function('(id)=>{const e=state.canvasEdges.find(e=>e.id===id);return e?.route_style==="straight" && Number(e?.line_width)===4}',arg=ids['e1'])
        page.locator('#edgeLabel').fill('Numbered flow')
        page.wait_for_timeout(400)
        page.wait_for_function("()=>[...document.querySelectorAll('.edge-label')].some(x=>x.textContent==='Numbered flow')")

        # Focusing Card prose after a connector must move selection/panel ownership to the Card.
        page.locator(f'.canvas-node[data-id="{ids["a"]}"] .node-title').focus()
        page.wait_for_function("()=>state.selectedEdgeId===null && document.querySelector('#canvasPanelTitle')?.textContent==='Card'")

        # Routing Points delete immediately with Delete; they do not open a side panel or confirmation.
        routing.click()
        assert page.locator('#canvasToolPanel').get_attribute('class').find('hidden') >= 0
        page.keyboard.press('Delete')
        page.wait_for_function('(id)=>!state.canvasNodes.some(n=>n.id===id)',arg=ids['r'])
        assert page.locator('#modal:not(.hidden)').count()==0
        assert not errors,errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v560_structure_zoom_and_developmental_split(scene_live_server, scene_browser):
    base=scene_live_server
    tab,_=_new_project(base,'5.6 Developmental Split')
    page,errors=_page(base,scene_browser)
    try:
        cid=page.evaluate("""async tabId=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:tabId})});
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:tabId})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""",tab['id'])
        tile=page.locator('.developmental-structural-tile').first
        before=tile.bounding_box(); assert before
        page.evaluate("()=>applyCanvasZoom(1.5,{persist:false})")
        page.wait_for_timeout(80)
        after=tile.bounding_box(); assert after
        assert after['width'] > before['width']*1.4
        assert after['height'] > before['height']*1.4

        # Structural deletion is explicit and distinct from deleting a normal Card.
        tile.click()
        page.wait_for_selector('#canvasToolPanel:not(.hidden)')
        delete_button=page.locator('#deleteNode')
        assert delete_button.inner_text()=='Delete Chapter Card'
        assert 'danger' in (delete_button.get_attribute('class') or '')
        delete_button.click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Delete Chapter Card'
        assert 'manuscript text will not be deleted' in page.locator('#modalBody').inner_text()
        page.locator('#modal button').filter(has_text='Cancel').click()

        # The same Developmental Canvas can be used from Manuscript Split View,
        # including its Structure Order instead of flattening everything into free cards.
        page.evaluate("()=>setView('manuscript')")
        page.evaluate('id=>openCanvasSplit(id)',cid)
        page.wait_for_selector('.split-developmental-lane')
        assert page.locator('.split-developmental-tile').count() >= 3
        assert page.locator('.split-developmental-workspace #splitCanvasStage').count()==1
        assert not errors,errors
    finally:
        _release(base)
        _shutdown_page(page)
