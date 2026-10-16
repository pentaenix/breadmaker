from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5416_refresh_restores_last_canvas_and_existing_viewport(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.16 Canvas refresh memory')

    page, errors = _page(base, scene_browser)
    ids = None
    try:
        ids = page.evaluate("""async()=>{
          const first=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'First Canvas',canvas_type:'blank'})});
          const second=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Remember Me',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(second.id);
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          applyCanvasZoom(.72,{persist:false});
          const stage=$('#canvasStage');stage.scrollLeft=430;stage.scrollTop=315;
          await persistCurrentCanvasViewport();
          return {first:first.id,second:second.id};
        }""")
        page.wait_for_function("id=>state.currentCanvas?.id===id", arg=ids['second'])
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)

    # A fresh browser boot models page refresh for the application state: UI state is
    # re-read from the project database rather than inherited from JS memory.
    page2, errors2 = _page(base, scene_browser)
    try:
        page2.wait_for_function("id=>state.currentCanvas?.id===id", arg=ids['second'], timeout=15000)
        page2.wait_for_timeout(500)
        page2.evaluate("()=>setView('canvas')")
        page2.wait_for_function("()=>!state.canvasViewportRestoring&&state.canvasPendingViewport===null")
        restored = page2.evaluate("""()=>({
          id:state.currentCanvas?.id,
          zoom:state.canvasZoom,
          left:$('#canvasStage').scrollLeft,
          top:$('#canvasStage').scrollTop,
          name:$('#canvasName').value
        })""")
        assert restored['id'] == ids['second']
        assert restored['name'] == 'Remember Me'
        assert abs(restored['zoom'] - .72) < .02
        assert abs(restored['left'] - 430) < 4
        assert abs(restored['top'] - 315) < 4

        # The remembered Canvas follows later navigation rather than being a one-time
        # startup choice.
        page2.evaluate("id=>selectCanvas(id)", ids['first'])
        page2.wait_for_function("id=>state.currentCanvas?.id===id", arg=ids['first'])
        assert not errors2, errors2
    finally:
        _release(base)
        _shutdown_page(page2)

    page3, errors3 = _page(base, scene_browser)
    try:
        page3.wait_for_function("id=>state.currentCanvas?.id===id", arg=ids['first'], timeout=15000)
        assert page3.evaluate("()=>$('#canvasName').value") == 'First Canvas'
        assert not errors3, errors3
    finally:
        _release(base)
        _shutdown_page(page3)
