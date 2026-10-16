from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass

def test_v723_task_cache_search_explanation_filter_and_create(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.3 tasks polish');page,errors=_page(base,scene_browser)
    try:
        page.evaluate("""async()=>{const t=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Prepare release',description:'General'})});await api(`/api/tasks/${t.id}/milestones`,{method:'POST',body:JSON.stringify({title:'French storefront',description:'Translate launch notes'})});setView('tasks');invalidateTaskCache();await loadTasks()}""")
        page.evaluate("""()=>{window.__taskCalls=0;const real=api;window.api=async(...args)=>{if(args[0]==='/api/tasks'&&(!args[1]||!args[1].method))window.__taskCalls++;return real(...args)}}""")
        search=page.locator('#taskSearch');search.fill('French');page.wait_for_function("()=>document.querySelectorAll('#taskList .task-row').length===1")
        assert 'Milestone match' in page.locator('#taskList .task-row').inner_text()
        search.fill('French store');page.wait_for_timeout(120)
        assert page.evaluate('()=>window.__taskCalls')==0
        search.fill('Brand new thing');page.wait_for_function("()=>!!document.querySelector('[data-search-task-add]')")
        page.locator('[data-search-task-add]').click();page.wait_for_selector('#taskName')
        assert page.locator('#taskName').input_value()=='Brand new thing'
        page.locator('#modalClose').click()
        page.locator('[data-task-filter="later"]').click();page.wait_for_timeout(160)
        assert page.evaluate("()=>state.taskFilter")=='later'
        assert page.evaluate("async()=>String((await api('/api/ui-state/task-filter')).value)")=='later'
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)

def test_v723_quick_open_is_discoverable_scrollable_keyboard_and_deep(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.3 quick open');page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Characters',canvas_type:'blank'})});const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:100,y:100,w:220,h:120,title:'Eugene leaves',body:'Arc beat'})});const t=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Release'})});const m=await api(`/api/tasks/${t.id}/milestones`,{method:'POST',body:JSON.stringify({title:'Translate launch',description:'French storefront'})});await refresh(false);return{cv:cv.id,card:card.id,task:t.id,m:m.id}}""")
        assert page.locator('#quickOpenBtn').count()==1
        page.keyboard.press('Control+K');page.wait_for_selector('#quickOpen:not(.hidden)')
        assert page.locator('#quickOpenResults').evaluate("el=>getComputedStyle(el).overflowY") in ('auto','scroll')
        page.locator('#quickOpenInput').fill('Eugene leaves');page.wait_for_function("()=>document.querySelectorAll('#quickOpenResults [data-quick-index]').length===1")
        assert 'Card' in page.locator('#quickOpenResults').inner_text()
        page.keyboard.press('Enter');page.wait_for_selector('#canvasView.active')
        page.wait_for_function("id=>state.selectedNodes.has(id)",arg=ids['card'])
        page.keyboard.press('Control+K');page.locator('#quickOpenInput').fill('Translate launch');page.wait_for_function("()=>document.querySelectorAll('#quickOpenResults [data-quick-index]').length===1")
        assert 'Milestone' in page.locator('#quickOpenResults').inner_text()
        page.keyboard.press('ArrowDown');page.keyboard.press('Enter');page.wait_for_selector('#tasksView.active')
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)

def test_v723_map_context_and_canvas_promptless_delete_hitbox(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.3 map and canvas polish');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async()=>{const a=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Map A'})});const la=await api(`/api/maps/${a.id}/lines`,{method:'POST',body:JSON.stringify({name:'A line'})});const b=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Map B'})});await api(`/api/maps/${b.id}/lines`,{method:'POST',body:JSON.stringify({name:'B line'})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Delete polish',canvas_type:'blank'})});const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:120,y:120,w:220,h:120,title:'Delete me'})});const line=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'figure',shape:'line',x:420,y:120,w:220,h:160,figure_line_x1:.05,figure_line_y1:.1,figure_line_x2:.95,figure_line_y2:.9})});await refresh(false);return{a:a.id,la:la.id,b:b.id,cv:cv.id,card:card.id,line:line.id}}""")
        page.evaluate("async id=>{setView('maps');await selectMap(id)}",setup['a']);page.wait_for_selector('#mapsView.active')
        page.evaluate("""()=>{state.mapZoom=1.35;renderMapVisual();const s=document.querySelector('#mapStage');s.scrollLeft=91;s.scrollTop=47;queueMapViewportPersist()}""");page.wait_for_timeout(180)
        page.evaluate("async id=>await selectMap(id)",setup['b']);page.evaluate("async id=>await selectMap(id)",setup['a']);page.wait_for_timeout(100)
        restored=page.evaluate("()=>({zoom:state.mapZoom,left:document.querySelector('#mapStage').scrollLeft,top:document.querySelector('#mapStage').scrollTop,line:state.currentMapLineId})")
        assert abs(restored['zoom']-1.35)<.01 and restored['left']>=80 and restored['line']==setup['la']
        page.evaluate("async id=>{setView('canvas');await selectCanvas(id);setCanvasMode('pointer')}",setup['cv']);page.evaluate("id=>selectOnlyNode(id)",setup['card'])
        page.keyboard.press('Delete');page.wait_for_function("id=>!state.canvasNodes.some(n=>n.id===id)",arg=setup['card'])
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        assert 'Undo' in page.locator('#toast').inner_text();page.locator('#toast .toast-action').click();page.wait_for_function("id=>state.canvasNodes.some(n=>n.id===id)",arg=setup['card'])
        page.evaluate("id=>selectOnlyNode(id,{inspect:false})",setup['line']);page.wait_for_timeout(60)
        hit=page.evaluate("""id=>{const el=document.querySelector(`.canvas-figure[data-id='${id}']`),r=el.getBoundingClientRect();const on=document.elementFromPoint(r.left+r.width*.5,r.top+r.height*.5),off=document.elementFromPoint(r.left+r.width*.1,r.top+r.height*.8);return{on:on?.className?.baseVal||on?.className||'',off:off?.className?.baseVal||off?.className||''}}""",setup['line'])
        assert 'figure-hit-line' in hit['on'] or 'figure-shape' in hit['on']
        assert 'figure-hit-line' not in hit['off'] and 'figure-shape' not in hit['off']
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)
