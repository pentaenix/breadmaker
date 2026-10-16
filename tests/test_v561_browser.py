from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v561_populate_settles_rows_from_actual_rendered_height(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.1 Populate rows')
    page,errors=_page(base,scene_browser)
    try:
        metrics=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Measured Populate',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);applyCanvasTextSize(28,{persist:false});
          const catalog=await canvasPopulateCatalog(),template=catalog.categories.flatMap(c=>c.templates).find(t=>t.id==='character_arc');
          await populateCanvas(template,{x:120,y:140});
          const created=state.canvasNodes.filter(n=>state.selectedNodes.has(n.id)).sort((a,b)=>a.id-b.id),columns=5;
          const first=created.slice(0,columns),second=created.slice(columns,columns*2);
          const heights=first.map(n=>document.querySelector(`.canvas-node[data-id=\"${n.id}\"]`).offsetHeight);
          const xs1=first.map(n=>n.x),xs2=second.map(n=>n.x);
          return {firstY:first[0].y,secondY:second[0].y,maxHeight:Math.max(...heights),xs1,xs2};
        }""")
        assert metrics['secondY'] >= metrics['firstY'] + metrics['maxHeight'] + 43
        for i,x in enumerate(metrics['xs2']):
            assert abs(x-metrics['xs1'][i]) < .6
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v561_space_pan_over_card_text_never_selects_page_text(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.1 Space pan')
    page,errors=_page(base,scene_browser)
    try:
        node_id=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Pan',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const n=await createNodeAt('rect',520,420,{title:'Drag across this title',body:'This body must never turn blue while Space is acting as the hand tool.',w:260,h:125},{inspect:false,label:'Pan Card'});
          const s=$('#canvasStage');s.scrollLeft=320;s.scrollTop=240;return n.id;
        }""")
        body=page.locator(f'.canvas-node[data-id="{node_id}"] .node-body')
        box=body.bounding_box(); assert box
        before=page.evaluate("()=>({left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop})")
        page.keyboard.down('Space')
        assert page.evaluate('()=>state.canvasSpaceHeld') is True
        page.mouse.move(box['x']+20,box['y']+10)
        page.mouse.down()
        page.mouse.move(box['x']-110,box['y']-50,steps=6)
        page.mouse.up()
        page.keyboard.up('Space')
        after=page.evaluate("()=>({left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop,selection:String(window.getSelection?.()||''),panning:$('#canvasStage').classList.contains('panning')})")
        assert after['left'] > before['left']+90
        assert after['selection']==''
        assert after['panning'] is False
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)



def test_v561_space_pan_recovers_when_card_text_still_has_focus(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.1 Focused Space pan')
    page,errors=_page(base,scene_browser)
    try:
        node_id=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Focused Pan',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const n=await createNodeAt('rect',540,440,{title:'Focused Card',body:'Keep this prose unchanged.',w:250,h:125},{inspect:false,label:'Focused Pan Card'});
          const s=$('#canvasStage');s.scrollLeft=360;s.scrollTop=250;return n.id;
        }""")
        body=page.locator(f'.canvas-node[data-id="{node_id}"] .node-body')
        body.click()
        assert page.evaluate("()=>document.activeElement?.classList.contains('node-body')") is True
        original=body.inner_text()
        page.keyboard.down('Space')
        # While an editor is focused the browser may begin a normal typed space, but
        # starting a hand gesture must convert that key-hold into pan and restore prose.
        page.wait_for_timeout(30)
        stage=page.locator('#canvasStage'); box=stage.bounding_box(); assert box
        before=page.evaluate("()=>({left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop})")
        page.mouse.move(box['x']+box['width']*.72,box['y']+box['height']*.72)
        page.mouse.down()
        page.mouse.move(box['x']+box['width']*.55,box['y']+box['height']*.60,steps=5)
        page.mouse.up()
        page.keyboard.up('Space')
        page.wait_for_timeout(450)
        after=page.evaluate("()=>({left:$('#canvasStage').scrollLeft,top:$('#canvasStage').scrollTop,lasso:!$('#selectionRect').classList.contains('hidden'),space:state.canvasSpaceHeld,key:state.canvasSpaceKeyDown})")
        assert after['left'] > before['left']+60
        assert body.inner_text()==original
        assert after['lasso'] is False
        assert after['space'] is False and after['key'] is False
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
