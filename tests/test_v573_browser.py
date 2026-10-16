from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v573_hover_nests_and_left_gutter_moves_out(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.7.3 Canvas nesting')
    page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{
          const a=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas A',canvas_type:'blank'})});
          const b=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas B',canvas_type:'blank'})});
          const child=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Canvas B1',canvas_type:'blank',parent_id:b.id})});
          await refresh(false);setView('canvas');await selectCanvas(a.id);return {a:a.id,b:b.id,child:child.id};
        }""")
        page.evaluate("""ids=>{
          const source=document.querySelector(`.canvas-item[data-id="${ids.b}"]`),target=document.querySelector(`.canvas-item[data-id="${ids.a}"]`),dt=new DataTransfer();
          source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));
          const r=target.getBoundingClientRect();
          target.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+r.width*.55,clientY:r.top+r.height*.5}));
        }""",ids)
        page.wait_for_timeout(650)
        target=page.locator(f'.canvas-item[data-id="{ids["a"]}"]')
        assert 'nest-ready' in (target.get_attribute('class') or '')
        page.evaluate("""ids=>{
          const target=document.querySelector(`.canvas-item[data-id="${ids.a}"]`),r=target.getBoundingClientRect(),dt=new DataTransfer();
          target.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+r.width*.55,clientY:r.top+r.height*.5}));
        }""",ids)
        page.wait_for_function("ids=>state.canvases.find(c=>c.id===ids.b)?.parent_id===ids.a",arg=ids)
        # Its own child stays attached after nesting.
        assert page.evaluate("ids=>state.canvases.find(c=>c.id===ids.child)?.parent_id",ids)==ids['b']
        page.evaluate("""ids=>{
          const source=document.querySelector(`.canvas-item[data-id="${ids.b}"]`),dt=new DataTransfer();
          source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));
        }""",ids)
        page.wait_for_selector('#canvasOutdentDrop')
        page.evaluate("""()=>{
          const zone=document.querySelector('#canvasOutdentDrop'),r=zone.getBoundingClientRect(),dt=new DataTransfer();
          zone.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+r.width/2,clientY:r.top+30}));
          zone.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+r.width/2,clientY:r.top+30}));
        }""")
        page.wait_for_function("ids=>state.canvases.find(c=>c.id===ids.b)?.parent_id==null",arg=ids)
        assert page.evaluate("ids=>state.canvases.find(c=>c.id===ids.child)?.parent_id",ids)==ids['b']
        root_ids=page.evaluate("()=>state.canvases.filter(c=>c.parent_id==null).sort((a,b)=>a.position-b.position||a.id-b.id).map(c=>c.id)")
        assert root_ids.index(ids['b'])==root_ids.index(ids['a'])+1
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
