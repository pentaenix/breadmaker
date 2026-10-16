from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v555_collapsed_canvas_labels_use_canvas_or_chapter_token(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.5.5 compact canvas labels')
    page, errors = _page(base, scene_browser)
    try:
        labels = page.evaluate("""async()=>{
          for(const name of ['Canvas 1','canvas 20: Plot','Canvas a - Arc','Chapter 3: Arrival','chapter B','1: Scratch','A Notes','Characters']){
            await api('/api/canvases',{method:'POST',body:JSON.stringify({name,description:'',canvas_type:'blank'})});
          }
          await refresh(false);setView('canvas');renderCanvasRail();
          return [...document.querySelectorAll('.canvas-item')].map(row=>({
            name:row.querySelector('.canvas-item-name')?.textContent,
            compact:row.querySelector('.canvas-item-initial')?.textContent,
          }));
        }""")
        got = {item['name']: item['compact'] for item in labels}
        assert got['Canvas 1'] == '1'
        assert got['canvas 20: Plot'] == '20'
        assert got['Canvas a - Arc'] == 'a'
        assert got['Chapter 3: Arrival'] == '3'
        assert got['chapter B'] == 'B'
        assert got['1: Scratch'] == '1'
        assert got['A Notes'] == 'A'
        assert got['Characters'] == 'C'
        assert not errors, errors
    finally:
        _shutdown_page(page)
