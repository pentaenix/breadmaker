from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v562_multi_selection_order_menu_and_layouts(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.2 Order selected')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Order',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          const nodes=[];
          nodes.push(await createNodeAt('rect',160,170,{title:'One',body:'short',w:180,h:90},{inspect:false}));
          nodes.push(await createNodeAt('rect',530,210,{title:'Two',body:'a taller card with more body text to measure',w:230,h:150},{inspect:false}));
          nodes.push(await createNodeAt('rect',290,510,{title:'Three',body:'third',w:200,h:110},{inspect:false}));
          nodes.push(await createNodeAt('rect',690,470,{title:'Four',body:'fourth',w:160,h:100},{inspect:false}));
          state.selectedNodes=new Set(nodes.map(n=>n.id));renderNodeSelection();updateSelectionInspector();return nodes.map(n=>n.id);
        }""")
        # The right-click menu on any member of a multi-selection owns the Order submenu.
        card=page.locator(f'.canvas-node[data-id="{ids[0]}"]')
        card.click(button='right')
        order=page.locator('#contextMenu button').filter(has_text='Order').first
        assert order.is_visible()
        order.hover()
        for label in ('Compact','Line vertical','Line horizontal','Circular'):
            assert page.locator('#contextMenu button').filter(has_text=label).first.is_visible()
        page.keyboard.press('Escape')

        vertical=page.evaluate("""async()=>{await orderSelectedCanvasNodes('vertical');return canvasOrderNodes().map(n=>({id:n.id,x:n.x,y:n.y,h:renderedNodeSize(n).h}))}""")
        assert max(p['x'] for p in vertical)-min(p['x'] for p in vertical)<.6
        ys=[p['y'] for p in vertical]
        assert ys==sorted(ys)
        for a,b in zip(vertical,vertical[1:]):
            assert b['y'] >= a['y']+a['h']+35

        horizontal=page.evaluate("""async()=>{await orderSelectedCanvasNodes('horizontal');return canvasOrderNodes().map(n=>({id:n.id,x:n.x,y:n.y,w:renderedNodeSize(n).w}))}""")
        assert max(p['y'] for p in horizontal)-min(p['y'] for p in horizontal)<.6
        xs=[p['x'] for p in horizontal]
        assert xs==sorted(xs)
        for a,b in zip(horizontal,horizontal[1:]):
            assert b['x'] >= a['x']+a['w']+35

        circular=page.evaluate("""async()=>{await orderSelectedCanvasNodes('circular');const ns=canvasOrderNodes();const pts=ns.map(n=>{const s=renderedNodeSize(n);return{x:n.x+s.w/2,y:n.y+s.h/2}});const cx=pts.reduce((a,p)=>a+p.x,0)/pts.length,cy=pts.reduce((a,p)=>a+p.y,0)/pts.length;return pts.map(p=>Math.hypot(p.x-cx,p.y-cy))}""")
        assert max(circular)-min(circular)<2.5

        compact=page.evaluate("""async()=>{await orderSelectedCanvasNodes('compact');return canvasOrderNodes().map(n=>({x:n.x,y:n.y,h:renderedNodeSize(n).h}))}""")
        assert len({round(p['y'],1) for p in compact})>=2
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
