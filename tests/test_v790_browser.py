from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v790_book_chapter_header_edits_in_place_and_keeps_identity(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'7.9.0 inline Chapter header')
    _set_blocks(base,ch['id'],['Prose stays attached'])
    task=requests.post(base+'/api/tasks',json={'title':'Linked','status':'open'},timeout=10).json()
    ref=requests.post(base+'/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'chapter','target_id':str(ch['id'])},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openWholeBook(false,false)')
        page.wait_for_function("document.querySelectorAll('.book-chapter-title').length>0")
        title=page.locator(f'.book-chapter-heading[data-chapter-id="{ch["id"]}"] .book-chapter-title')
        assert title.get_attribute('contenteditable')=='true'
        title.click()
        page.keyboard.press('ControlOrMeta+A')
        page.keyboard.press('Backspace')
        assert requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).status_code==200
        page.keyboard.type('Renamed In Full Text')
        page.wait_for_function("document.querySelector('.book-chapter-title').innerText==='Renamed In Full Text'")
        page.evaluate('flushSave()')
        page.wait_for_function("!state.saveInFlight && !state.dirty")
        saved=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()
        assert saved['chapter']['id']==ch['id']
        assert saved['chapter']['title']=='Renamed In Full Text'
        assert saved['blocks'][0]['text_content']=='Prose stays attached'
        refs=requests.get(base+f"/api/references?source_type=task&source_id={task['id']}",timeout=10).json()
        linked=next(row for row in refs if row['id']==ref['id'])
        assert int(linked['target_id'])==ch['id']
        assert linked['target_label']=='Renamed In Full Text'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v790_maps_empty_state_offers_create_map_button(scene_live_server,scene_browser):
    base=scene_live_server; _tab,_ch=_new_project(base,'7.9.0 Maps empty state')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click()
        page.wait_for_function("document.querySelector('#mapsView').classList.contains('active')")
        page.wait_for_function("!document.querySelector('#mapEmpty').classList.contains('hidden')")
        title=page.locator('#mapCurrentName')
        assert title.inner_text()=='Maps'
        assert 'map-title-placeholder' in (title.get_attribute('class') or '')
        pencil=page.locator('#editMapDetailsBtn')
        assert not pencil.is_visible()
        canvas_placeholder_color=page.evaluate("getComputedStyle(document.querySelector('#canvasName'),'::placeholder').color")
        map_placeholder_color=page.evaluate("getComputedStyle(document.querySelector('#mapCurrentName')).color")
        assert map_placeholder_color==canvas_placeholder_color
        button=page.locator('#emptyCreateMap')
        assert button.is_visible()
        button.click()
        page.wait_for_function("document.querySelector('#modalTitle').textContent==='New Map'")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
