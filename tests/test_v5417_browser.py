from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page
from test_v540_browser import _open_structure, _release


def test_v5417_dirty_version_switch_can_create_new_version(scene_live_server, scene_browser):
    base = scene_live_server
    tab, ch1 = _new_project(base, '5.4.17 Create version on switch')
    page, errors = _page(base, scene_browser)
    try:
        setup = page.evaluate("""async d=>{
          const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});
          const ch3=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          return{canvas:cv.id,ch2:ch2.id,ch3:ch3.id}
        }""", {'tab': tab['id']})
        page.wait_for_selector('#developmentalStructureDock')

        _open_structure(page)
        page.locator('#newCanvasVersion').click()
        page.locator('#dialogText').fill('Alternate')
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Alternate'")
        alternate_id = page.evaluate("()=>state.activeCanvasVersionId")

        moving = page.locator('.developmental-structural-tile').filter(has_text='Chapter 3').first
        target = page.locator('.developmental-structural-tile').filter(has_text='Chapter 1').first
        mb, tb = moving.bounding_box(), target.bounding_box()
        assert mb and tb
        page.mouse.move(mb['x'] + mb['width']/2, mb['y'] + mb['height']/2)
        page.mouse.down()
        page.mouse.move(tb['x'] + 6, tb['y'] + tb['height']/2, steps=7)
        page.mouse.up()
        page.evaluate("()=>state.structuralChain.then(()=>true)")
        assert page.evaluate("()=>state.activeCanvasVersion?.working_dirty") is True

        _open_structure(page)
        page.locator('.canvas-version-use').filter(has_text='Version 1').first.click()
        page.wait_for_selector('#createVersionSwitch')
        assert page.locator('#modalTitle').inner_text() == 'This version has changes'
        assert page.locator('#discardVersionSwitch').is_visible()
        assert page.locator('#createVersionSwitch').is_visible()
        assert page.locator('#updateVersionSwitch').is_visible()

        page.locator('#createVersionSwitch').click()
        page.wait_for_selector('#dialogText')
        page.locator('#dialogText').fill('Alternate Copy')
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Version 1'")

        versions = requests.get(base + f"/api/canvases/{setup['canvas']}/versions", timeout=10).json()['versions']
        copy = next(v for v in versions if v['name'] == 'Alternate Copy')
        saved_copy = requests.get(base + f"/api/canvases/{setup['canvas']}/versions/{copy['id']}", timeout=10).json()
        copy_order = [i['entity_id'] for i in sorted((i for i in saved_copy['items'] if i['placed']), key=lambda i: i['lane_position'])]
        assert copy_order == [setup['ch3'], ch1['id'], setup['ch2']]

        saved_alternate = requests.get(base + f"/api/canvases/{setup['canvas']}/versions/{alternate_id}", timeout=10).json()
        alternate_order = [i['entity_id'] for i in sorted((i for i in saved_alternate['items'] if i['placed']), key=lambda i: i['lane_position'])]
        assert alternate_order == [ch1['id'], setup['ch2'], setup['ch3']]

        manuscript = [c['id'] for c in requests.get(base + '/api/bootstrap', timeout=10).json()['chapters'] if c['tab_id'] == tab['id'] and not c.get('is_implicit')]
        assert manuscript == [ch1['id'], setup['ch2'], setup['ch3']]
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
