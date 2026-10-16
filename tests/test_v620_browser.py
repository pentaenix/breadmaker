from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v620_maps_ui_parity_and_experimental_renderer(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps standardization')
    map1=requests.post(base+'/api/maps',json={'name':'Story Map','description':'Main story map details'},timeout=10).json()
    map2=requests.post(base+'/api/maps',json={'name':'Second Map'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{map1['id']}/lines",json={'name':'Main Plot','description':'Primary story line'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        # Rail parity: disclosure/grip/more and no old inline +.
        row=page.locator(f'.map-tree-map-row[data-map-id="{map1["id"]}"]')
        assert row.locator('.map-tree-toggle').inner_text().strip() in {'▸','▾'}
        assert row.locator('.map-tree-grip').count()==1
        assert row.locator('.map-tree-more').count()==1
        assert row.locator('.map-tree-line-add').count()==0
        if row.locator('.map-tree-toggle').inner_text().strip()=='▸':
            row.locator('.map-tree-toggle').click();page.wait_for_timeout(80)
        line_row=page.locator(f'.map-tree-line-row[data-map-tree-line="{line["id"]}"]')
        assert line_row.locator('.map-tree-line-grip').count()==1
        assert line_row.locator('.map-tree-line-more').count()==1
        assert page.locator('#mapRail').evaluate("el=>Math.round(el.getBoundingClientRect().width)")==236
        page.locator('#toggleMapRail').click();page.wait_for_timeout(180)
        assert page.locator('#mapRail').evaluate("el=>Math.round(el.getBoundingClientRect().width)")==58
        assert row.locator('.map-tree-more').evaluate("el=>getComputedStyle(el).display")=="none"
        assert line_row.locator('.map-tree-line-more').evaluate("el=>getComputedStyle(el).display")=="none"
        page.locator('#toggleMapRail').click();page.wait_for_timeout(180)
        # Hover details match the Chapter side flyer.
        row.hover();page.wait_for_timeout(360)
        fly=page.locator('#chapterDetailsHover');assert fly.count()==1 and 'Main story map details' in fly.inner_text()
        # Toolbar title pencil opens existing details inspector.
        page.locator('#editMapDetailsBtn').click();page.wait_for_selector('#mapInspectorName')
        assert page.locator('#mapInspectorName').input_value()=='Story Map'
        page.locator('[data-close-map-inspector]').click()
        # View is a popover, not a modal, and owns zoom/ruler.
        page.locator('#mapViewBtn').click();page.wait_for_selector('#mapViewPopover:not(.hidden)')
        assert page.locator('#mapZoomFit').count()==1
        page.locator('#mapViewRuler').select_option('percent');page.wait_for_timeout(300)
        assert requests.get(base+f"/api/maps/{map1['id']}",timeout=10).json()['map']['ruler']=='percent'
        assert page.locator('#mapSvg .map-ruler-percent .map-ruler-label').count()>=2
        page.locator('#mapViewBtn').click()
        # Renderer is hidden by default and enabled from Project Settings -> Experimental Features.
        assert page.locator('#mapMetroViewBtn').evaluate("el=>getComputedStyle(el).display")=='none'
        page.evaluate("()=>openProjectSettings()")
        page.locator('#settingsExperimental').click();page.wait_for_selector('#experimentalMapRenderer')
        toggle=page.locator('#experimentalMapRenderer');assert toggle.get_attribute('aria-checked')=='false'
        toggle.click();page.wait_for_timeout(180);assert toggle.get_attribute('aria-checked')=='true'
        page.locator('#modalClose').click();page.wait_for_timeout(120);page.locator('#modalClose').click();page.wait_for_timeout(120)
        assert page.locator('#mapMetroViewBtn').evaluate("el=>getComputedStyle(el).display")!='none'
        page.locator('#mapMetroViewBtn').hover();page.wait_for_timeout(80);assert page.locator('#mapRendererTooltip').evaluate("el=>!el.classList.contains('hidden')")
        # New line spans the full story axis.
        detail=requests.get(base+f"/api/maps/{map1['id']}",timeout=10).json();created=next(x for x in detail['lines'] if x['id']==line['id'])
        assert created['planned_start']==0 and created['planned_end']==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
