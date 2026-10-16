from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _opacity(locator):
    return float(locator.evaluate("el=>getComputedStyle(el).opacity"))


def test_v6311_station_hover_suppresses_segment_connector(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.11 station hover priority')
    story=requests.post(base+'/api/maps',json={'name':'Priority','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Source','planned_start':.05,'planned_end':.95,'schematic_start':.05,'schematic_end':.95},timeout=10).json()
    station=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Middle','planned_start':.5,'schematic_start':.5},timeout=10).json()
    requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Target','planned_start':.1,'planned_end':.9,'schematic_start':.1,'schematic_end':.9},timeout=10)

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        affordances=page.locator(f'#mapSvg [data-map-line-connect-affordance="{line["id"]}"]')
        assert affordances.count()==2
        first=affordances.nth(0)
        handle=first.locator('.map-line-connect-handle'); hb=handle.bounding_box(); assert hb
        route=page.locator(f'#mapSvg [data-map-line-route="{line["id"]}"]').first.bounding_box(); assert route
        page.mouse.move(hb['x']+hb['width']/2,route['y']+route['height']/2);page.wait_for_timeout(410)
        assert _opacity(first)>.9

        stop=page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner'); sb=stop.bounding_box(); assert sb
        page.mouse.move(sb['x']+sb['width']/2,sb['y']+sb['height']/2);page.wait_for_timeout(140)
        assert all(_opacity(affordances.nth(i))<.1 for i in range(affordances.count()))
        stop.click();page.wait_for_timeout(180)
        assert page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-station-connect-handle').count()==1
        assert all(_opacity(affordances.nth(i))<.1 for i in range(affordances.count()))
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
