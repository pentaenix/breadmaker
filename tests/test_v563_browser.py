from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v563_connector_label_marker_and_multi_selection_ui(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.3 Connector polish')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Flow marker',description:'',color:'#446688'})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Connector polish',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          const a=await createNodeAt('rect',160,170,{title:'One',body:'Body',w:190,h:100},{inspect:false});
          const b=await createNodeAt('rect',520,170,{title:'Two',body:'Body',w:190,h:100},{inspect:false});
          const c=await createNodeAt('rect',880,170,{title:'Three',body:'Body',w:190,h:100},{inspect:false});
          const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left',label:'Relative label'})});
          state.canvasEdges.push(edge);drawEdges();return{marker:marker.id,a:a.id,b:b.id,c:c.id,edge:edge.id};
        }""")
        page.evaluate('id=>selectEdge(id,true)',ids['edge'])
        page.wait_for_selector('#edgeColorChoice')
        assert page.locator('#edgeColorChoice option').filter(has_text='Flow marker').count()==1

        page.evaluate("()=>applyCanvasTextSize(12,{persist:false})")
        page.wait_for_timeout(40)
        before=float(page.locator('.edge-label').evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        page.evaluate("()=>applyCanvasTextSize(24,{persist:false})")
        page.wait_for_timeout(60)
        after=float(page.locator('.edge-label').evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        assert after > before*1.4

        page.locator('#edgeColorChoice').select_option(f"marker:{ids['marker']}")
        page.wait_for_function('(x)=>{const e=state.canvasEdges.find(e=>e.id===x.edge);return +e.marker_id===+x.marker && e.color==="#446688"}',arg={'edge':ids['edge'],'marker':ids['marker']})

        none=page.locator('[data-edge-choice="end_style"][data-edge-value="none"]')
        assert none.locator('svg.connector-end-none-svg').count()==1
        assert none.locator('.connector-end-icon.none').count()==0

        page.evaluate("ids=>{state.selectedNodes=new Set(ids);state.selectedEdgeId=null;renderNodeSelection();updateSelectionInspector()}",[ids['a'],ids['b'],ids['c']])
        body=page.locator('#canvasPanelBody').inner_text()
        assert 'Drag any selected object' not in body
        assert 'move with the selection' not in body
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
