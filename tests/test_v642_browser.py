from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v642_collapsed_maps_only_show_selected_map_lines_as_color_swatches(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.4.2 compact Line swatches')
    first=requests.post(base+'/api/maps',json={'name':'First Map'},timeout=10).json()
    second=requests.post(base+'/api/maps',json={'name':'Second Map'},timeout=10).json()
    line1=requests.post(base+f"/api/maps/{first['id']}/lines",json={'name':'Blue Line','color':'#3366cc'},timeout=10).json()
    line2=requests.post(base+f"/api/maps/{second['id']}/lines",json={'name':'Red Line','color':'#cc3344'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{first["id"]}"]').click();page.wait_for_timeout(150)
        page.locator('#toggleMapRail').click();page.wait_for_timeout(120)

        first_line=page.locator(f'[data-map-tree-line="{line1["id"]}"]')
        second_line=page.locator(f'[data-map-tree-line="{line2["id"]}"]')
        assert first_line.is_visible()
        assert second_line.count()==0
        assert first_line.locator('.map-tree-line-short').is_hidden()
        dot=first_line.locator('.map-tree-line-dot')
        assert dot.is_visible()
        style=dot.evaluate("el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return{bg:s.backgroundColor,w:r.width,h:r.height}}")
        assert style['bg']=='rgb(51, 102, 204)'
        assert 25 <= style['w'] <= 27 and 7 <= style['h'] <= 9

        page.locator(f'.map-tree-map-row[data-map-id="{second["id"]}"]').click();page.wait_for_timeout(180)
        assert page.locator(f'[data-map-tree-line="{line1["id"]}"]').count()==0
        second_line=page.locator(f'[data-map-tree-line="{line2["id"]}"]')
        assert second_line.is_visible()
        assert second_line.locator('.map-tree-line-short').is_hidden()
        assert second_line.locator('.map-tree-line-dot').is_visible()
        assert second_line.locator('.map-tree-line-dot').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(204, 51, 68)'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
