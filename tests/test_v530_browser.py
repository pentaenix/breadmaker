from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v530_canvas_view_menu_card_toolbar_and_per_canvas_presentation(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'5.3 Canvas UI')
    first=requests.post(base+'/api/canvases',json={'name':'Plot Board','canvas_type':'blank'},timeout=10).json()
    second=requests.post(base+'/api/canvases',json={'name':'Characters','canvas_type':'blank'},timeout=10).json()
    requests.post(base+f"/api/canvases/{first['id']}/nodes",json={'shape':'rect','title':'Opening','body':'The bakery opens','x':120,'y':100,'w':220,'h':125},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click()
        page.locator('#canvasView').wait_for(state='visible')
        assert page.locator('#toggleRail').get_attribute('class').find('nav-collapse-button')>=0
        assert page.locator('#addCanvas').get_attribute('class').find('bare-button')>=0
        assert page.locator('#addNodePanelBtn').get_attribute('aria-label')=='Add Card'
        assert page.locator('#openLegend').count()==0

        page.locator('#canvasViewBtn').click()
        pop=page.locator('#canvasViewPopover'); pop.wait_for(state='visible')
        assert page.locator('#canvasLegendToggle').is_visible()
        page.locator('#canvasLegendToggle').click()
        assert page.locator('#canvasLegendToggle').get_attribute('aria-checked')=='true'
        assert page.locator('#canvasLegendDrawer').is_visible()

        page.locator('#canvasTextSize').evaluate("(el)=>{el.value='24';el.dispatchEvent(new Event('input',{bubbles:true}))}")
        page.locator('#canvasFont').select_option('courier')
        page.wait_for_timeout(60)
        card=page.locator('.canvas-node:not(.routing-node):not(.label-node):not(.image-object)').first
        size=card.evaluate("el=>getComputedStyle(el).fontSize")
        family=card.evaluate("el=>getComputedStyle(el).fontFamily")
        assert size=='24px'
        assert 'Courier' in family

        # Viewport controls live in View and update the displayed zoom without closing it.
        before=page.locator('#canvasZoomValue').inner_text()
        page.locator('#canvasZoomIn').click()
        assert page.locator('#canvasViewPopover').is_visible()
        assert page.locator('#canvasZoomValue').inner_text()!=before

        # Switching Canvas restores presentation preferences per Canvas.
        page.locator('.canvas-item').filter(has_text='Characters').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Characters'")
        page.locator('#canvasViewBtn').click()
        assert page.locator('#canvasLegendToggle').get_attribute('aria-checked')=='false'
        assert page.locator('#canvasFont').input_value()=='arial'
        assert float(page.locator('#canvasTextSize').input_value()) != 24
        page.locator('#canvasViewBtn').click()  # close

        page.locator('.canvas-item').filter(has_text='Plot Board').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Plot Board'")
        page.locator('#canvasViewBtn').click()
        assert page.locator('#canvasLegendToggle').get_attribute('aria-checked')=='true'
        assert page.locator('#canvasFont').input_value()=='courier'
        assert float(page.locator('#canvasTextSize').input_value())==24

        # Sidebar collapse uses the same compact pattern as Manuscript.
        page.locator('#canvasViewBtn').click()
        page.locator('#toggleRail').click()
        assert 'collapsed' in (page.locator('#canvasRail').get_attribute('class') or '')
        assert not page.locator('#addCanvas').is_visible()
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
