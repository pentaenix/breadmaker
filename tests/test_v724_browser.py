from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass

def test_v724_quick_open_single_visible_entry(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.4 quick open polish');page,errors=_page(base,scene_browser)
    try:
        page.locator('#brandButton').click();page.wait_for_selector('#projectMenu:not(.hidden)')
        assert 'Quick Open' not in page.locator('#projectMenu').inner_text()
        page.keyboard.press('Escape')
        size=page.locator('#quickOpenBtn').evaluate("el=>{const s=getComputedStyle(el);return{w:parseFloat(s.width),h:parseFloat(s.height),font:parseFloat(s.fontSize)}}")
        assert size['w']>=31 and size['h']>=31 and size['font']>=20
        page.locator('#quickOpenBtn').click();page.wait_for_selector('#quickOpen:not(.hidden)')
        page.keyboard.press('Escape');page.keyboard.press('Control+K');page.wait_for_selector('#quickOpen:not(.hidden)')
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)

def test_v724_undo_toast_offers_redo(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.2.4 redo toast');page,errors=_page(base,scene_browser)
    try:
        ids=page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Redo toast',canvas_type:'blank'})});const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:100,y:100,w:220,h:120,title:'Delete redo'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);setCanvasMode('pointer');selectOnlyNode(card.id);return{cv:cv.id,card:card.id}}""")
        page.keyboard.press('Delete');page.wait_for_function("id=>!state.canvasNodes.some(n=>n.id===id)",arg=ids['card'])
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent?.includes('Undo')")
        page.locator('#toast .toast-action').click();page.wait_for_function("id=>state.canvasNodes.some(n=>n.id===id)",arg=ids['card'])
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent?.includes('Redo')")
        page.locator('#toast .toast-action').click();page.wait_for_function("id=>!state.canvasNodes.some(n=>n.id===id)",arg=ids['card'])
        assert not errors,errors
    finally:_shutdown_page(page);_release(base)
