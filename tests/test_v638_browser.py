from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _opacity(locator):
    return float(locator.evaluate("el=>getComputedStyle(el).opacity"))


def test_v638_station_splits_line_into_independent_hover_connectors(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.8 segmented connector')
    story=requests.post(base+'/api/maps',json={'name':'Segmented','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Source','planned_start':.1,'planned_end':.9,'schematic_start':.1,'schematic_end':.9},timeout=10).json()
    station=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Divider','planned_start':.5,'schematic_start':.5},timeout=10).json()
    requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Target','planned_start':.1,'planned_end':.9,'schematic_start':.1,'schematic_end':.9},timeout=10)

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        affordances=page.locator(f'#mapSvg [data-map-line-connect-affordance="{line["id"]}"]')
        assert affordances.count()==2
        handles=page.locator(f'#mapSvg [data-map-line-connect="{line["id"]}"]')
        xs=sorted((handles.nth(i).bounding_box()['x']+handles.nth(i).bounding_box()['width']/2) for i in range(handles.count()))
        sb=page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner').bounding_box();assert sb
        station_x=sb['x']+sb['width']/2
        assert xs[0] < station_x < xs[1]

        route=page.locator(f'#mapSvg [data-map-line-route="{line["id"]}"]').first
        rb=route.bounding_box();assert rb
        route_y=rb['y']+rb['height']/2
        page.mouse.move(20,20);page.wait_for_timeout(500)
        page.mouse.move(xs[0],route_y);page.wait_for_timeout(410)
        assert _opacity(affordances.nth(0)) > .9
        assert _opacity(affordances.nth(1)) < .1
        page.mouse.move(xs[1],route_y);page.wait_for_timeout(410)
        assert _opacity(affordances.nth(0)) < .1
        assert _opacity(affordances.nth(1)) > .9
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v638_dense_populated_line_does_not_force_connectors_into_tight_station_gaps(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.8 populated segments')
    story=requests.post(base+'/api/maps',json={'name':'Generated','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Generated Line','planned_start':.495,'planned_end':.505,'schematic_start':.495,'schematic_end':.505},timeout=10).json()
    items=[]
    for idx in range(9):
        items.append(requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':f'Generated {idx+1}','planned_start':.499+idx*.0002,'schematic_start':.499+idx*.0002},timeout=10).json())

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        # Segment detection still follows rendered geometry, but the current
        # affordance threshold intentionally keeps knobs out of dense Station gaps.
        assert page.locator(f'#mapSvg [data-map-line-connect-affordance="{line["id"]}"]').count()==0
        stop=page.locator(f'#mapSvg [data-map-item="{items[4]["id"]}"] .map-stop-inner')
        stop.click();page.wait_for_timeout(160)
        assert page.locator(f'#mapSvg [data-map-item="{items[4]["id"]}"] .map-station-connect-handle').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
