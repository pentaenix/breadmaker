from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v566_dusk_has_clear_surface_hierarchy_without_changing_light_or_night(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.6 Dusk hierarchy')
    page,errors=_page(base,scene_browser)
    try:
        cv=page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Dusk Canvas',canvas_type:'blank'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);const idx=await createLabelNode(220,180);return {canvas:cv.id,index:idx.id}}""")
        index=page.locator(f'.canvas-node.label-node[data-id="{cv["index"]}"]')

        page.evaluate("setTheme('light')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='light'")
        light=page.evaluate("""()=>{const r=getComputedStyle(document.documentElement);return {bg:r.getPropertyValue('--bg').trim(),surface:r.getPropertyValue('--surface').trim(),surface2:r.getPropertyValue('--surface2').trim(),text:r.getPropertyValue('--text').trim()}}""")
        assert light=={'bg':'#f3f3f1','surface':'#fff','surface2':'#f8f8f7','text':'#202020'}

        page.evaluate("setTheme('dusk')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dusk'")
        dusk=page.evaluate("""()=>{const r=getComputedStyle(document.documentElement);return {bg:r.getPropertyValue('--bg').trim(),surface:r.getPropertyValue('--surface').trim(),surface2:r.getPropertyValue('--surface2').trim(),text:r.getPropertyValue('--text').trim(),accent:r.getPropertyValue('--accent').trim()}}""")
        assert dusk=={'bg':'#92938d','surface':'#d8d4cb','surface2':'#c6c4bc','text':'#282923','accent':'#59695b'}
        assert page.locator('.canvas-stage').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(146, 147, 141)'
        assert page.locator('.canvas-toolbar').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(216, 212, 203)'
        assert page.locator('.canvas-rail').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(198, 196, 188)'
        assert index.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(81, 88, 80)'
        assert index.evaluate("el=>getComputedStyle(el).color")=='rgb(245, 243, 236)'

        page.evaluate("setTheme('dark')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dark'")
        dark=page.evaluate("""()=>{const r=getComputedStyle(document.documentElement);return {bg:r.getPropertyValue('--bg').trim(),surface:r.getPropertyValue('--surface').trim(),surface2:r.getPropertyValue('--surface2').trim(),text:r.getPropertyValue('--text').trim()}}""")
        assert dark=={'bg':'#191a19','surface':'#222322','surface2':'#292a29','text':'#ececea'}
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
