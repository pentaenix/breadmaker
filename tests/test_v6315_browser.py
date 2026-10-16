from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _center(locator):
    box=locator.bounding_box(); assert box
    return box['x']+box['width']/2,box['y']+box['height']/2


def test_v6315_crowded_drop_moves_only_direct_collision_cluster(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.15 local reflow')
    story=requests.post(base+'/api/maps',json={'name':'Local Reflow','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Line','planned_start':.1,'planned_end':.9},timeout=10).json()
    stations=[]
    for title,p in [('A',.15),('B',.30),('C',.50),('D',.62)]:
        stations.append(requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':title,'planned_start':p,'schematic_start':p},timeout=10).json())
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        stops=[page.locator(f'#mapSvg [data-map-item="{row["id"]}"] .map-stop-inner') for row in stations]
        before=[_center(stop) for stop in stops]
        ax,ay=before[0];bx,by=before[1];cx,cy=before[2];dx,dy=before[3]
        # Drop D only a few pixels to B's right. B and D conflict directly;
        # A and C are separated by roomy gaps and must remain untouched.
        target=bx+15
        page.mouse.move(dx,dy);page.mouse.down();page.mouse.move(target,by,steps=8);page.mouse.up();page.wait_for_timeout(700)
        after=[_center(stop) for stop in stops]
        ax2,_=after[0];bx2,_=after[1];cx2,_=after[2];dx2,_=after[3]
        assert abs(ax2-ax) < 1.0,(ax,ax2)
        assert abs(cx2-cx) < 1.0,(cx,cx2)
        assert bx2 < dx2 < cx2,(bx2,dx2,cx2)
        assert bx2 < bx-1.0,(bx,bx2)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v6315_minimum_station_spacing_is_about_thirty_percent_tighter(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.15 tighter station spacing')
    story=requests.post(base+'/api/maps',json={'name':'Spacing','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Dense','planned_start':.45,'planned_end':.55},timeout=10).json()
    one=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'One','planned_start':.5,'schematic_start':.5},timeout=10).json()
    two=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Two','planned_start':.50001,'schematic_start':.50001},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        a=page.locator(f'#mapSvg [data-map-item="{one["id"]}"] .map-stop-inner')
        b=page.locator(f'#mapSvg [data-map-item="{two["id"]}"] .map-stop-inner')
        ax,_=_center(a);bx,_=_center(b);gap=abs(bx-ax)
        # 6.3.14 used ~39 px at 100% zoom. The new ~70% rule lands near
        # 27 px while still keeping the 26 px Station circles separated.
        assert 26.5 <= gap <= 30.5,gap
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
