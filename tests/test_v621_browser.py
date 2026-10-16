from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v621_maps_view_density_ruler_font_and_lock(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.2.1')
    story=requests.post(base+'/api/maps',json={'name':'Story Map','ruler':'chapters'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Main Plot'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        # Expanded rail is a fixed parity width even with the inspector open.
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(100)
        page.locator(f'.map-tree-line-row[data-map-tree-line="{line["id"]}"]').click();page.wait_for_timeout(100)
        assert page.locator('#mapRail').evaluate("el=>Math.round(el.getBoundingClientRect().width)")==236
        assert page.locator('#mapRail').evaluate("el=>getComputedStyle(el).flexShrink")=='0'

        # Lock button exists in both states and directly toggles without opening another inspector flow.
        lock=page.locator(f'.map-tree-line-row[data-map-tree-line="{line["id"]}"] .map-tree-lock')
        assert 'Lock' in (lock.get_attribute('aria-label') or '')
        lock.click();page.wait_for_timeout(250)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();assert next(x for x in detail['lines'] if x['id']==line['id'])['layout_locked']==1
        lock=page.locator(f'.map-tree-line-row[data-map-tree-line="{line["id"]}"] .map-tree-lock')
        assert 'Unlock' in (lock.get_attribute('aria-label') or '')
        lock.click();page.wait_for_timeout(250)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();assert next(x for x in detail['lines'] if x['id']==line['id'])['layout_locked']==0

        # View uses compact cycling controls and no longer owns the per-Map ruler.
        page.locator('#mapViewBtn').click();page.wait_for_selector('#mapViewPopover:not(.hidden)')
        assert page.locator('#mapViewRuler').count()==0
        assert page.locator('.map-view-cycle-button').count()==3
        orientation=page.locator('#mapViewOrientation')
        before=orientation.locator('.map-view-cycle-value').inner_text().strip()
        orientation.click();page.wait_for_timeout(240)
        after=page.locator('#mapViewOrientation .map-view-cycle-value').inner_text().strip()
        assert {before,after}=={'Horizontal','Vertical'}

        # Map typography mirrors the other View menus and affects rendered SVG labels.
        page.locator('#mapTextSize').evaluate("el=>{el.value='22';el.dispatchEvent(new Event('input',{bubbles:true}))}");page.wait_for_timeout(120)
        assert page.locator('#mapTextSizeValue').inner_text().strip()=='22 px'
        assert page.evaluate("()=>getComputedStyle(document.documentElement).getPropertyValue('--map-label-font-size').trim()")=='22px'
        page.locator('#mapFont').select_option('times');page.wait_for_timeout(120)
        assert 'Times New Roman' in page.evaluate("()=>getComputedStyle(document.documentElement).getPropertyValue('--map-font-family')")
        page.locator('#mapViewBtn').click()

        # Ruler is a Map action, and choosing it changes only the current Map setting.
        row=page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]')
        row.locator('.map-tree-more').click();page.wait_for_timeout(80)
        ruler_btn=page.locator('#contextMenu button',has_text='Ruler')
        ruler_btn.hover();page.wait_for_timeout(120)
        page.locator('.context-submenu button',has_text='Percentage').click();page.wait_for_timeout(260)
        assert requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()['map']['ruler']=='percent'
        assert page.locator('#mapSvg .map-ruler-percent .map-ruler-label').count()>=2
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
