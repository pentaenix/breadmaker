from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass

def test_v725_visual_consistency_and_checkbox(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.5 consistency');page,errors=_page(base,scene_browser)
    try:
        page.evaluate("async()=>{const m=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Continuity Map'})});await refresh(false);setView('maps');await selectMap(m.id)}")
        assert page.locator('#mapCurrentName').inner_text()=='Continuity Map'
        sizes=page.evaluate("""()=>['#activeDocumentTitle','#canvasName','#mapCurrentName'].map(sel=>{const e=document.querySelector(sel),s=getComputedStyle(e);return{size:s.fontSize,weight:s.fontWeight}})""")
        assert len({x['size'] for x in sizes})==1
        page.evaluate("()=>setView('tasks')")
        page.evaluate("async()=>{await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Checkbox task'})});invalidateTaskCache();await loadTasks()}")
        appearance=page.locator('.task-check input').first.evaluate("el=>getComputedStyle(el).appearance")
        assert appearance=='none'
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)

def test_v725_task_delete_undo_redo_and_quick_search(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.5 behavior');page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{const t=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Quiet cleanup',description:'description-only needle'})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Arc notes',canvas_type:'blank'})});const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:100,y:100,w:220,h:120,title:'Visible title',body:'hidden permanence needle'})});await refresh(false);setView('tasks');invalidateTaskCache();await loadTasks();return{task:t.id,card:card.id}}""")
        # Task deletion is immediate and undoable.
        page.locator('[data-task-more]').first.click();page.wait_for_selector('#contextMenu:not(.hidden)')
        page.get_by_role('button',name='Delete',exact=True).last.click()
        page.wait_for_function("id=>!state.taskCache?.some(t=>t.id===id)",arg=ids['task'])
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        assert 'Undo' in page.locator('#toast').inner_text()
        page.locator('#toast .toast-action').click();page.wait_for_function("id=>Array.isArray(state.taskCache)&&state.taskCache.some(t=>t.id===id)",arg=ids['task'])
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent?.includes('Redo')")
        page.locator('#toast .toast-action').click();page.wait_for_function("()=>document.querySelector('#toast')?.textContent?.includes('Undo')")
        # Quick Open finds body/description text rather than only visible labels.
        page.keyboard.press('Control+K');page.locator('#quickOpenInput').fill('permanence needle')
        page.wait_for_function("()=>document.querySelectorAll('#quickOpenResults [data-quick-index]').length===1")
        text=page.locator('#quickOpenResults').inner_text();assert 'Visible title' in text and 'Arc notes' in text
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)
