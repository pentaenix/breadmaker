from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _center(locator):
    box=locator.bounding_box(); assert box
    return box['x']+box['width']/2,box['y']+box['height']/2


def test_v6314_crowded_station_neighbors_wait_until_pointer_release(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.14 deferred station reflow')
    story=requests.post(base+'/api/maps',json={'name':'Deferred','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Dense','planned_start':.45,'planned_end':.56},timeout=10).json()
    stations=[]
    for idx,p in enumerate((.5000,.5001,.5002)):
        stations.append(requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':chr(65+idx),'planned_start':p,'schematic_start':p},timeout=10).json())

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        a=page.locator(f'#mapSvg [data-map-item="{stations[0]["id"]}"] .map-stop-inner')
        b=page.locator(f'#mapSvg [data-map-item="{stations[1]["id"]}"] .map-stop-inner')
        c=page.locator(f'#mapSvg [data-map-item="{stations[2]["id"]}"] .map-stop-inner')
        ax,ay=_center(a);bx,by=_center(b);cx,cy=_center(c)
        target=(ax+bx)/2
        page.mouse.move(cx,cy);page.mouse.down();page.mouse.move(target,ay,steps=8);page.wait_for_timeout(160)

        # During the pull only the Station being held moves. The Line around it
        # stays visually stable; no neighboring reflow happens until release.
        ax_drag,_=_center(a);bx_drag,_=_center(b);cx_drag,_=_center(c)
        assert abs(ax_drag-ax) < 1.0,(ax,ax_drag)
        assert abs(bx_drag-bx) < 1.0,(bx,bx_drag)
        assert abs(cx_drag-target) < 4.0,(target,cx_drag)

        page.mouse.up();page.wait_for_timeout(700)
        ax2,_=_center(a);bx2,_=_center(b);cx2,_=_center(c)
        assert ax2 < cx2 < bx2,(ax2,cx2,bx2)
        assert bx2 > bx_drag + .1,(bx_drag,bx2)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
