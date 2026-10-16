from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v637_dense_populated_style_line_discovers_connector_from_station_hover(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.7 populated line connector')
    story=requests.post(base+'/api/maps',json={'name':'Dense Generated Layout','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    # Deliberately tiny logical width. Readability spacing stretches the rendered
    # route substantially, matching the geometry produced by dense populated Lines.
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Generated','planned_start':.495,'planned_end':.505,'schematic_start':.495,'schematic_end':.505},timeout=10).json()
    items=[]
    for idx in range(9):
        items.append(requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':f'Generated {idx+1}','planned_start':.499+idx*.0002,'schematic_start':.499+idx*.0002},timeout=10).json())
    target=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Target','planned_start':.2,'planned_end':.8,'schematic_start':.2,'schematic_end':.8},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        affordance=page.locator(f'#mapSvg [data-map-line-connect-affordance="{line["id"]}"]')
        # 6.3.11 intentionally superseded the temporary 6.3.7 behavior: dense
        # Station gaps no longer receive Line connectors, and Stations own hover.
        assert affordance.count()==0
        station=page.locator(f'#mapSvg [data-map-item="{items[4]["id"]}"] .map-stop-inner')
        station.click();page.wait_for_timeout(180)
        assert page.locator(f'#mapSvg [data-map-item="{items[4]["id"]}"] .map-station-connect-handle').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v637_backspace_deletes_selected_connection_without_confirmation_and_undo_restores_it(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.7 direct connection delete')
    story=requests.post(base+'/api/maps',json={'name':'Delete Connections','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    a=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'A','planned_start':0,'planned_end':1,'schematic_start':0,'schematic_end':1},timeout=10).json()
    b=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'B','planned_start':0,'planned_end':1,'schematic_start':0,'schematic_end':1},timeout=10).json()
    junction=requests.post(base+f"/api/maps/{story['id']}/junctions",json={'kind':'merge','from_line_id':a['id'],'from_edge':'point','from_planned_position':.4,'from_schematic_position':.4,'to_line_id':b['id'],'to_edge':'point','to_planned_position':.6,'to_schematic_position':.6},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        path=page.locator(f'#mapSvg [data-map-junction="{junction["id"]}"]')
        path.click();page.wait_for_timeout(120)
        assert not page.locator('#mapInspector').is_visible()
        page.keyboard.press('Backspace');page.wait_for_timeout(420)
        assert page.locator('#modalOverlay:not(.hidden)').count()==0
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert not any(j['id']==junction['id'] for j in detail['junctions'])

        # Deletion remains reversible rather than protected by modal friction.
        page.keyboard.press('ControlOrMeta+z');page.wait_for_timeout(520)
        restored=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert any(j['id']==junction['id'] for j in restored['junctions'])
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
