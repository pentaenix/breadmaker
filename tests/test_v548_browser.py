from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v548_structure_uses_delayed_manuscript_style_descriptions_and_slim_tiles(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.9 Manuscript UI consistency')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async tab=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'A Real Title',tab_id:tab})});
          const canvas=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Structure UI',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(canvas.id);
        }""", tab['id'])
        page.wait_for_selector('.developmental-structural-tile')
        tile = page.locator('.developmental-structural-tile').filter(has_text='A Real Title').first
        box = tile.bounding_box()
        assert box and abs(box['width'] - 143) <= 1.5 and abs(box['height'] - 52) <= 1.5

        page.locator('#canvasStructureBtn').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        assert page.locator('.structure-help-button').count() == 0
        assert page.locator('[data-structure-help]').count() == 0

        targets = [
            page.locator('#structureOrderToggle'),
            page.locator('#newCanvasVersion'),
            page.locator('#reviewStructureChanges'),
        ]
        for target in targets:
            target.hover()
            # Manuscript descriptive hovers are deliberately delayed, not instant.
            page.wait_for_timeout(180)
            assert page.locator('#structurePanelHoverCard:not(.hidden)').count() == 0
            page.wait_for_timeout(180)
            tip = page.locator('#structurePanelHoverCard:not(.hidden)')
            assert tip.count() == 1
            tb = target.bounding_box(); hb = tip.bounding_box()
            assert tb and hb and hb['x'] + hb['width'] <= tb['x'] - 4
            page.mouse.move(30, 30)
            page.wait_for_function("()=>document.querySelector('#structurePanelHoverCard')?.classList.contains('hidden')")

        # The actual interactive controls are inside those natural hover surfaces.
        assert page.locator('#structureOrderToggle').get_attribute('role') == 'switch'
        assert page.locator('#newCanvasVersion').inner_text().strip() == '+'
        assert page.locator('#newCanvasVersion').get_attribute('title') is None
        assert page.locator('#reviewStructureChanges').get_attribute('title') is None
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
