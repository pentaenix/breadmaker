from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v6317_populate_menu_loads_before_open_and_map_details_are_modal_autosave(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.17 details populate')
    story=requests.post(base+'/api/maps',json={'name':'Story Map','layout_mode':'scaled','label_mode':'minimal','ruler':'chapters'},timeout=10).json()
    requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Story Line'},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        row=page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]');row.wait_for()
        row.locator('.map-tree-more').click();page.wait_for_timeout(180)
        menu=page.locator('#contextMenu')
        assert menu.is_visible()
        assert 'Populate' in menu.inner_text()
        assert 'Loading…' not in menu.inner_text()
        assert 'Auto Arrange' not in menu.inner_text()
        populate=menu.locator('.context-menu-row',has_text='Populate').first
        populate.hover();page.wait_for_timeout(80)
        submenu=populate.locator('.context-submenu').first
        assert submenu.is_visible()
        assert 'Loading…' not in submenu.inner_text()
        page.keyboard.press('Escape')

        row.click(button='right');page.wait_for_timeout(180)
        assert menu.is_visible()
        assert 'Populate' in menu.inner_text()
        assert 'Loading…' not in menu.inner_text()
        page.keyboard.press('Escape')

        page.locator('#editMapDetailsBtn').click();page.wait_for_timeout(80)
        assert page.locator('#modal').is_visible()
        assert page.locator('#modalTitle').inner_text()=='Map Details'
        assert page.locator('#mapDetailsName').is_visible()
        assert page.locator('#mapDetailsDescription').is_visible()
        assert page.locator('#mapDetailsBackgroundMode').is_visible()
        assert page.locator('#modalActions').inner_text().strip()==''
        assert page.locator('#saveMapInspector').count()==0
        assert page.locator('#duplicateMapInspector').count()==0
        assert page.locator('#deleteMapInspector').count()==0

        name=page.locator('#mapDetailsName');name.fill('Renamed Map');name.press('Tab');page.wait_for_timeout(250)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert detail['map']['name']=='Renamed Map'
        page.locator('#mapDetailsBackgroundMode').select_option('custom');page.wait_for_timeout(50)
        assert page.locator('#mapDetailsCustomBackground').is_visible()
        color=page.locator('#mapDetailsBackgroundText');color.fill('#123456');color.press('Tab');page.wait_for_timeout(250)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert detail['map']['background'].lower()=='#123456'
        page.locator('#mapDetailsBackgroundMode').select_option('default');page.wait_for_timeout(250)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert detail['map']['background'].lower()=='#f3f3f1'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v6317_canvas_and_maps_collapsed_selection_match_manuscript_treatment(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'6.3.17 collapsed selectors')
    canvas=requests.post(base+'/api/canvases',json={'name':'Canvas 3'},timeout=10).json()
    story=requests.post(base+'/api/maps',json={'name':'Map 4'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click();page.wait_for_timeout(180)
        page.locator(f'.canvas-item[data-id="{canvas["id"]}"]').click();page.wait_for_timeout(80)
        page.locator('#toggleRail').click();page.wait_for_timeout(100)
        crow=page.locator(f'.canvas-item[data-id="{canvas["id"]}"]')
        cshort=crow.locator('.canvas-item-initial')
        assert cshort.is_visible() and cshort.inner_text()=='3'
        cstyle=crow.evaluate("el=>{const s=getComputedStyle(el);return{h:el.getBoundingClientRect().height,b:s.boxShadow,bg:s.backgroundColor}}")
        assert 39 <= cstyle['h'] <= 41
        assert cstyle['b']!='none'

        page.locator('[data-view="maps"]').click();page.wait_for_timeout(180)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(80)
        page.locator('#toggleMapRail').click();page.wait_for_timeout(100)
        mrow=page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]')
        mshort=mrow.locator('.map-tree-map-short')
        assert mshort.is_visible() and mshort.inner_text()=='4'
        mstyle=mrow.evaluate("el=>{const s=getComputedStyle(el);return{h:el.getBoundingClientRect().height,b:s.boxShadow,bg:s.backgroundColor}}")
        assert 39 <= mstyle['h'] <= 41
        assert mstyle['b']!='none'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
