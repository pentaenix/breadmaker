from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v553_canvas_pencil_tracks_title_width(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.5.3 canvas title pencil')
    page, errors = _page(base, scene_browser)
    try:
        widths = page.evaluate("""async()=>{
          const short=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Map',description:'',canvas_type:'blank'})});
          await refresh(false);setView('canvas');await selectCanvas(short.id);
          await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
          const input=document.querySelector('#canvasName');
          const pencil=document.querySelector('#editCanvasDetailsBtn');
          const shortInput=input.getBoundingClientRect();
          const shortPencil=pencil.getBoundingClientRect();
          const shortGap=shortPencil.left-shortInput.right;
          const shortWidth=shortInput.width;
          await api(`/api/canvases/${short.id}`,{method:'PATCH',body:JSON.stringify({name:'A Much Longer Planning Canvas'})});
          await selectCanvas(short.id);
          await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
          const longInput=input.getBoundingClientRect();
          const longPencil=pencil.getBoundingClientRect();
          return {shortGap,shortWidth,longGap:longPencil.left-longInput.right,longWidth:longInput.width};
        }""")
        assert widths['shortWidth'] < 60, widths
        assert widths['longWidth'] > widths['shortWidth'] + 80, widths
        assert 0 <= widths['shortGap'] <= 4, widths
        assert 0 <= widths['longGap'] <= 4, widths
        assert not errors, errors
    finally:
        _shutdown_page(page)
