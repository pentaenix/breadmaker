from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v572_canvas_rows_match_manuscript_hierarchy_affordances(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.7.2 Canvas navigator')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const a=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas A',canvas_type:'blank'})});
          const b=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas B',canvas_type:'blank'})});
          const child=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas A1',canvas_type:'blank',parent_id:a.id})});
          await refresh(false);setView('canvas');await selectCanvas(a.id);return {a:a.id,b:b.id,child:child.id};
        }""")
        parent=page.locator(f'.canvas-item[data-id="{ids["a"]}"]')
        assert parent.locator('.canvas-grip').inner_text().strip()=='⋮⋮'
        assert parent.get_attribute('draggable')=='true'
        toggle=parent.locator('.canvas-tree-toggle')
        assert toggle.inner_text().strip()=='▾'
        toggle.click(); assert toggle.inner_text().strip()=='▸'
        toggle.click(); assert page.locator(f'.canvas-item[data-id="{ids["child"]}"]').count()==1
        # Dragging the row via the Chapter-style reorder affordance changes sibling order.
        source=page.locator(f'.canvas-item[data-id="{ids["b"]}"]')
        target=page.locator(f'.canvas-item[data-id="{ids["a"]}"]')
        source.drag_to(target,target_position={"x":80,"y":2})
        page.wait_for_function("ids=>state.canvases.filter(c=>c.parent_id==null).sort((a,b)=>a.position-b.position)[0].id===ids.b", arg=ids)
        root_ids=page.locator('#canvasRailItems > .canvas-item-wrap > .canvas-item').evaluate_all("els=>els.map(el=>+el.dataset.id)")
        assert root_ids[:2]==[ids['b'],ids['a']]
        assert page.locator(f'.canvas-item[data-id="{ids["child"]}"]').count()==1
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
