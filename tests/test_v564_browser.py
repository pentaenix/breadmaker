from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v564_lasso_selects_only_fully_enclosed_connectors_and_bulk_styles(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.4 Connector multi-select')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Connector selection',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          const make=(title,x,y)=>createNodeAt('rect',x,y,{title,body:'',w:100,h:80},{inspect:false});
          const a=await make('A',100,100),b=await make('B',500,100),c=await make('C',100,300),d=await make('D',500,300);
          const e1=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left',route_style:'straight'})});
          const e2=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:c.id,to_node_id:d.id,from_port:'right',to_port:'left',route_style:'straight'})});
          state.canvasEdges.push(e1,e2);state.selectedNodes.clear();clearCanvasEdgeSelection();await setCanvasMode('pointer',{persist:false});renderNodeSelection();drawEdges();closeCanvasPanel();
          return{e1:e1.id,e2:e2.id};
        }""")
        page.wait_for_selector('.edge-group')

        # A rectangle that only crosses the middle of both lines must select neither.
        partial=page.evaluate("""()=>{const m=canvasWorldMetrics();return {sx:m.left+250*m.scaleX,sy:m.top+90*m.scaleY,ex:m.left+450*m.scaleX,ey:m.top+390*m.scaleY}}""")
        page.mouse.move(partial['sx'],partial['sy']);page.mouse.down();page.mouse.move(partial['ex'],partial['ey'],steps=5);page.mouse.up()
        page.wait_for_timeout(80)
        assert page.evaluate('()=>canvasSelectedEdgeIds().size')==0

        # This encloses the complete line paths but not the Cards themselves.
        full=page.evaluate("""()=>{const m=canvasWorldMetrics();return {sx:m.left+195*m.scaleX,sy:m.top+90*m.scaleY,ex:m.left+505*m.scaleX,ey:m.top+390*m.scaleY}}""")
        page.mouse.move(full['sx'],full['sy']);page.mouse.down();page.mouse.move(full['ex'],full['ey'],steps=6);page.mouse.up()
        page.wait_for_function('()=>canvasSelectedEdgeIds().size===2 && state.selectedNodes.size===0')
        assert page.locator('#canvasPanelTitle').inner_text()=='Selected Connectors'

        # Change both lines together.
        page.locator('[data-edge-choice="line_style"][data-edge-value="dashed"]').click()
        page.wait_for_function('ids=>ids.every(id=>state.canvasEdges.find(e=>e.id===id)?.line_style==="dashed")',arg=[ids['e1'],ids['e2']])
        page.locator('[data-edge-choice="route_style"][data-edge-value="curved"]').click()
        page.wait_for_function('ids=>ids.every(id=>state.canvasEdges.find(e=>e.id===id)?.route_style==="curved")',arg=[ids['e1'],ids['e2']])

        # Custom color must paint both the selected line shaft and arrowhead.
        page.locator('#multiEdgeColorChoice').select_option('custom')
        page.locator('#multiEdgeColorCustom').evaluate("el=>{el.value='#2457a6';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function('ids=>ids.every(id=>state.canvasEdges.find(e=>e.id===id)?.color==="#2457a6")',arg=[ids['e1'],ids['e2']])
        page.wait_for_timeout(80)
        shaft=page.locator(f'.edge-group[data-edge-id="{ids["e1"]}"] .edge-line')
        head=page.locator(f'.edge-group[data-edge-id="{ids["e1"]}"] .edge-end-visible')
        assert shaft.get_attribute('stroke')=='#2457a6'
        assert head.get_attribute('fill')=='#2457a6'
        computed=shaft.evaluate("el=>getComputedStyle(el).stroke")
        assert computed in ('rgb(36, 87, 166)','#2457a6')
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
