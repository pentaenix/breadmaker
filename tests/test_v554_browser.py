from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v554_canvas_overflow_hidden_when_rail_collapses(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, "5.5.4 collapsed Canvas overflow")
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Plot Board',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
        }""")
        more = page.locator('.canvas-item-more').first
        assert more.evaluate("el=>getComputedStyle(el).display") == 'grid'
        page.locator('#toggleRail').click()
        page.wait_for_function("()=>document.querySelector('#canvasRail').classList.contains('collapsed')")
        assert more.evaluate("el=>getComputedStyle(el).display") == 'none'
        assert not errors, errors
    finally:
        _shutdown_page(page)
