from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v634-browser'},timeout=2)
    except Exception: pass


def test_v634_edge_station_remains_selectable_and_long_line_gets_delayed_middle_connector(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.4 line connectors')
    story=requests.post(base+'/api/maps',json={'name':'Connection Handles','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    source=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Source','planned_start':0,'planned_end':1,'schematic_start':0,'schematic_end':1},timeout=10).json()
    edge=requests.post(base+f"/api/map-lines/{source['id']}/items",json={'item_type':'stop','title':'Edge Station','planned_start':0,'schematic_start':0},timeout=10).json()
    target=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Target','planned_start':0,'planned_end':1,'schematic_start':0,'schematic_end':1},timeout=10).json()
    target_station=requests.post(base+f"/api/map-lines/{target['id']}/items",json={'item_type':'stop','title':'Target Station','planned_start':.62,'schematic_start':.62},timeout=10).json()
    medium=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Medium','planned_start':.20,'planned_end':.30,'schematic_start':.20,'schematic_end':.30},timeout=10).json()
    short=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Short','planned_start':.45,'planned_end':.47,'schematic_start':.45,'schematic_end':.47},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)

        # Select Source so its endpoint resize handles are present. When a
        # Station owns that exact endpoint the resize handle becomes an outer
        # stroke-only ring, leaving the Station center interactive.
        page.locator(f'[data-map-tree-line="{source["id"]}"]').click();page.wait_for_timeout(120)
        start_handle=page.locator(f'#mapSvg [data-map-line-handle="{source["id"]}"][data-line-edge="start"]')
        assert start_handle.count()==1
        assert 'item-overlap' in (start_handle.get_attribute('class') or '')
        assert start_handle.get_attribute('data-edge-item')==str(edge['id'])
        page.locator(f'#mapSvg [data-map-item="{edge["id"]}"] .map-stop-inner').click();page.wait_for_timeout(150)
        assert page.locator(f'#mapSvg [data-map-item="{edge["id"]}"] .map-station-connect-handle').count()==1

        # This Line has only an endpoint Station, so it still owns one eligible
        # route segment. Its connector remains hidden until 300 ms of hover.
        route=page.locator(f'#mapSvg [data-map-line-route="{source["id"]}"]').first
        affordance=page.locator(f'#mapSvg [data-map-line-connect-affordance="{source["id"]}"]')
        assert affordance.count()==1
        assert page.locator(f'#mapSvg [data-map-line-connect-affordance="{medium["id"]}"]').count()==1
        assert page.locator(f'#mapSvg [data-map-line-connect-affordance="{short["id"]}"]').count()==0
        page.mouse.move(20,20);page.wait_for_timeout(560)
        assert float(affordance.evaluate("el=>getComputedStyle(el).opacity")) < .1
        connector_handle=page.locator(f'#mapSvg [data-map-line-connect="{source["id"]}"]')
        cb=connector_handle.bounding_box();rb=route.bounding_box();assert cb and rb
        page.mouse.move(cb['x']+cb['width']/2,rb['y']+rb['height']/2);page.wait_for_timeout(180)
        assert float(affordance.evaluate("el=>getComputedStyle(el).opacity")) < .1
        page.wait_for_timeout(240)
        assert float(affordance.evaluate("el=>getComputedStyle(el).opacity")) > .9

        # The center knob uses the same direct drag-to-connect language as a
        # Station/Span and persists a route-point junction rather than a fake
        # Station at the middle of the Line.
        handle=page.locator(f'#mapSvg [data-map-line-connect="{source["id"]}"]')
        hb=handle.bounding_box();tb=page.locator(f'#mapSvg [data-map-item="{target_station["id"]}"] .map-stop-outer').bounding_box();assert hb and tb
        page.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2);page.mouse.down()
        page.mouse.move(tb['x']+tb['width']/2,tb['y']+tb['height']/2,steps=9);page.mouse.up();page.wait_for_timeout(420)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        junction=next(j for j in detail['junctions'] if j['from_line_id']==source['id'] and j['to_item_id']==target_station['id'])
        assert junction['from_item_id'] is None and junction['to_line_id']==target['id']
        assert junction['from_edge']=='point' and junction['to_edge']=='point'
        assert abs(float(junction['from_planned_position'])-.5) < .02
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
