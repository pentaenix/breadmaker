from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v552_manuscript_and_canvas_pencils_open_existing_details(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.5.2 title details')
    page, errors = _page(base, scene_browser)
    try:
        page.locator('#editActiveDocumentBtn').click()
        page.wait_for_selector('#tabConfigName')
        assert page.locator('#tabConfigName').input_value() == tab['title']
        page.locator('#tabConfigName').fill('Renamed Manuscript Tab')
        page.locator('#saveTabConfig').click()
        page.wait_for_function("document.querySelector('#activeDocumentTitle')?.textContent==='Renamed Manuscript Tab'")

        cid = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Board Before',description:'Planning notes',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""")
        assert page.locator('#canvasName').get_attribute('readonly') is not None
        page.locator('#editCanvasDetailsBtn').click()
        page.wait_for_selector('#canvasConfigName')
        assert page.locator('#canvasConfigName').input_value() == 'Board Before'
        assert page.locator('#canvasConfigDescription').input_value() == 'Planning notes'
        page.locator('#canvasConfigName').fill('Board After')
        page.locator('#saveCanvasConfig').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Board After'")
        assert page.evaluate('()=>state.currentCanvas.id') == cid
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
