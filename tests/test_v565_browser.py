from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v565_theme_cycle_index_colors_and_canvas_view_parity(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.6.5 Theme parity')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("setTheme('light')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='light'")
        cv=page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Theme Canvas',canvas_type:'blank'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);const idx=await createLabelNode(220,180);return {canvas:cv.id,index:idx.id}}""")
        page.wait_for_selector(f'.canvas-node.label-node[data-id="{cv["index"]}"]')
        index=page.locator(f'.canvas-node.label-node[data-id="{cv["index"]}"]')
        assert index.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(255, 255, 255)'
        assert index.evaluate("el=>getComputedStyle(el).color")=='rgb(32, 32, 32)'

        # Existing button cycles Light -> Dusk -> Dark -> Light, no dropdown.
        page.locator('#themeBtn').click()
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dusk'")
        dusk_bg=page.locator('body').evaluate("el=>getComputedStyle(el).backgroundColor")
        assert dusk_bg=='rgb(170, 167, 159)'
        assert index.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(85, 87, 82)'
        page.wait_for_function("()=>document.querySelector('#nodeBackground')?.value==='#555752' && document.querySelector('#nodeTextColor')?.value==='#f4f4f0'")

        page.locator('#themeBtn').click()
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dark'")
        assert index.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(0, 0, 0)'
        assert index.evaluate("el=>getComputedStyle(el).color")=='rgb(255, 255, 255)'
        page.locator('#themeBtn').click()
        page.wait_for_function("()=>document.documentElement.dataset.theme==='light'")

        # Custom Index colors stay literal when cycling themes.
        page.evaluate("""async id=>{const n=state.canvasNodes.find(n=>n.id===id);await applyNodePatch(n,{color:'#7b2d8e',text_color:'#f0e5a4'},'Custom Index');renderNodes()}""",cv['index'])
        page.locator('#themeBtn').click(); page.locator('#themeBtn').click()
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dark'")
        assert index.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(123, 45, 142)'
        assert index.evaluate("el=>getComputedStyle(el).color")=='rgb(240, 229, 164)'

        # Canvas rail matches Manuscript expanded width.
        widths=page.evaluate("()=>({canvas:parseFloat(getComputedStyle(document.querySelector('.canvas-rail')).width),book:parseFloat(getComputedStyle(document.querySelector('.book-nav')).width)})")
        assert widths['canvas']==widths['book']==236

        # Fit is an icon in the same zoom-control row, not a text action below it.
        assert page.locator('#canvasZoomFit').evaluate("el=>el.parentElement.classList.contains('canvas-view-zoom-controls')")
        assert page.locator('#canvasZoomFit svg').count()==1
        assert page.locator('#canvasZoomFit').inner_text().strip()==''
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
