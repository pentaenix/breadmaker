from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v547_structure_panel_tooltips_switch_scope_and_compact_tiles(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.7 Structure presentation')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async d=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'A Real Title',tab_id:d.tab})});
          const chapter=state.chapters.find(ch=>+ch.tab_id===+d.tab);
          await selectChapter(chapter.id,false,false);
          const scene=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'Opening beat',tab_id:d.tab})});
          const chapters=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Chapter Structure',canvas_type:'developmental',developmental_scope:'chapters'})});
          const scenes=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Scene Structure',canvas_type:'developmental',developmental_scope:'scenes'})});
          await refresh(false);setView('canvas');await selectCanvas(chapters.id);
          return{chapters:chapters.id,scenes:scenes.id,scene:scene.id};
        }""", {'tab': tab['id']})
        page.wait_for_selector('.developmental-structural-tile')

        # Scope belongs once in the lane header, not inside every structural tile.
        assert 'STRUCTURE ORDER (Chapter)' in page.locator('.developmental-structure-head').inner_text()
        scope_style = page.locator('#developmentalStructureScope').evaluate("el=>({fontStyle:getComputedStyle(el).fontStyle,color:getComputedStyle(el).color})")
        assert scope_style['fontStyle'] == 'italic'
        tile = page.locator('.developmental-structural-tile').filter(has_text='A Real Title').first
        box = tile.bounding_box()
        assert box and abs(box['width'] - 143) <= 1.5 and abs(box['height'] - 52) <= 1.5
        assert tile.locator('.developmental-tile-kind').count() == 0

        # Normal creation tools remain grouped before the Developmental-only Structure control.
        assert page.evaluate("()=>document.querySelector('#addImage').compareDocumentPosition(document.querySelector('#canvasStructureBtn')) & Node.DOCUMENT_POSITION_FOLLOWING")

        page.locator('#canvasStructureBtn').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        toggle = page.locator('#structureOrderToggle')
        assert toggle.evaluate("el=>el.tagName") == 'BUTTON'
        assert toggle.get_attribute('role') == 'switch'
        assert toggle.get_attribute('aria-checked') == 'true'
        assert 'Show Structure' in page.locator('.structure-panel-order').inner_text()
        assert page.locator('#newCanvasVersion').inner_text().strip() == '+'
        assert page.locator('[data-structure-help]').count() == 0
        assert page.locator('[data-structure-description]').count() >= 3

        toggle.click()
        page.wait_for_function("()=>document.querySelector('#structureOrderToggle').getAttribute('aria-checked')==='false' && document.querySelector('#developmentalStructureDock').classList.contains('hidden')")
        toggle.click()
        page.wait_for_function("()=>document.querySelector('#structureOrderToggle').getAttribute('aria-checked')==='true' && !document.querySelector('#developmentalStructureDock').classList.contains('hidden')")

        # Scene-based Developmental canvases advertise Scene scope in exactly the same header.
        page.evaluate("async id=>{await selectCanvas(id)}", ids['scenes'])
        page.wait_for_selector('.developmental-structural-tile')
        assert 'STRUCTURE ORDER (Scene)' in page.locator('.developmental-structure-head').inner_text()
        assert page.locator('.developmental-structural-tile .developmental-tile-kind').count() == 0
        scene_box = page.locator('.developmental-structural-tile').filter(has_text='Opening beat').first.bounding_box()
        assert scene_box and abs(scene_box['width'] - 143) <= 1.5
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
