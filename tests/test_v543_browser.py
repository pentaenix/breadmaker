from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v543_structural_tile_lists_and_opens_its_bound_chapter(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.3 Structural Chapter Link')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async d=>{
          const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'The Right Chapter',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return{chapter:ch2.id,canvas:cv.id};
        }""", {'tab': tab['id']})
        page.wait_for_function("()=>[...document.querySelectorAll('.developmental-structural-tile')].some(el=>el.textContent.includes('The Right Chapter'))")
        page.locator('.developmental-structural-tile').filter(has_text='The Right Chapter').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Chapter Structure'")
        page.wait_for_selector('.structural-binding-row')

        row = page.locator('.structural-binding-row')
        assert 'The Right Chapter' in row.inner_text()
        assert 'Chapter' in row.inner_text()
        assert 'Structural link' in row.inner_text()
        assert row.locator('[data-delete-ref]').count() == 0
        assert 'No links yet' not in page.locator('#canvasPanelBody').inner_text()

        # The structural binding is a real navigation target, not decorative text.
        row.locator('[data-open-ref]').click()
        page.wait_for_function("d=>document.querySelector('#manuscriptView')?.classList.contains('active') && +state.chapter?.id===d", arg=ids['chapter'])
        assert page.locator('#chapterTitle').inner_text().strip() == 'The Right Chapter'
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v543_structural_tile_lists_and_opens_its_bound_scene(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.3 Structural Scene Link')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async d=>{
          const chapter=state.chapters.find(ch=>+ch.tab_id===+d.tab);
          await selectChapter(chapter.id,false,false);
          const scene=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'The Right Scene',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Scene Development',canvas_type:'developmental',developmental_scope:'scenes'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return{scene:scene.id,chapter:chapter.id,canvas:cv.id};
        }""", {'tab': tab['id']})
        page.wait_for_function("()=>[...document.querySelectorAll('.developmental-structural-tile')].some(el=>el.textContent.includes('The Right Scene'))")
        page.locator('.developmental-structural-tile').filter(has_text='The Right Scene').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Scene Structure'")
        page.wait_for_selector('.structural-binding-row')
        row = page.locator('.structural-binding-row')
        assert 'The Right Scene' in row.inner_text()
        assert 'Scene' in row.inner_text()
        assert row.locator('[data-delete-ref]').count() == 0
        row.locator('[data-open-ref]').click()
        page.wait_for_function("d=>document.querySelector('#manuscriptView')?.classList.contains('active') && +state.chapter?.id===+d.chapter && !!(document.querySelector(`.ghost-scene-title[data-scene-id=\"${d.scene}\"]`)||document.querySelector(`.manuscript-block-wrap[data-scene-id=\"${d.scene}\"]`))", arg=ids)
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
