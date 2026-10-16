from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _center(locator):
    box=locator.bounding_box(); assert box
    return box['x']+box['width']/2,box['y']+box['height']/2


def test_v6313_crowded_station_drag_reorders_and_reflows_neighbors(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.13 station reflow')
    story=requests.post(base+'/api/maps',json={'name':'Reflow','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
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
        assert ax < bx < cx
        # Move C into the visual gap between A and B. There is effectively no
        # logical room there, so Breadmaker must establish A,C,B and push B
        # toward the room C vacated on the right rather than rejecting the drop.
        page.mouse.move(cx,cy);page.mouse.down();page.mouse.move((ax+bx)/2,ay,steps=8);page.mouse.up();page.wait_for_timeout(700)
        ax2,_=_center(a);bx2,_=_center(b);cx2,_=_center(c)
        assert ax2 < cx2 < bx2,(ax2,cx2,bx2)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        row=next(row for row in detail['lines'] if row['id']==line['id'])
        by_id={item['id']:item for item in row['items']}
        assert by_id[stations[1]['id']]['planned_start'] > .5001
        assert by_id[stations[2]['id']]['planned_start'] < by_id[stations[1]['id']]['planned_start']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v6313_invalid_connection_drop_shows_toast_instead_of_failing_silently(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.13 connection feedback')
    story=requests.post(base+'/api/maps',json={'name':'Feedback','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    first=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'First'},timeout=10).json()
    requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Second'},timeout=10).raise_for_status()
    station=requests.post(base+f"/api/map-lines/{first['id']}/items",json={'item_type':'stop','title':'Source','planned_start':.4,'schematic_start':.4},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        stop=page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner');stop.click();page.wait_for_timeout(160)
        knob=page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-station-connect-handle')
        x,y=_center(knob);page.mouse.move(x,y);page.mouse.down();page.mouse.move(35,35,steps=6);page.mouse.up();page.wait_for_timeout(220)
        toast=page.locator('#toast')
        assert 'Drop the connection on another Line, Station, or Span.' in toast.inner_text()
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)

def test_v6313_unlocked_populated_line_uses_writer_positions_not_source_order(scene_live_server,scene_browser):
    base=scene_live_server
    _,chapter=_new_project(base,'Maps 6.3.13 populated order')
    boot=requests.get(base+'/api/bootstrap',timeout=10).json();tab=next(d for d in boot['tabs'] if d.get('is_manuscript'))
    for title in ('Two','Three','Four'):
        requests.post(base+'/api/chapters',json={'title':title,'tab_id':tab['id']},timeout=10).raise_for_status()
    story=requests.post(base+'/api/maps',json={'name':'Populated','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    populated=requests.post(base+f"/api/maps/{story['id']}/populate",json={'template_id':'chapters'},timeout=10).json()
    requests.patch(base+f"/api/map-lines/{populated['line_id']}",json={'layout_locked':False},timeout=10).raise_for_status()
    detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();line=next(row for row in detail['lines'] if row['id']==populated['line_id'])
    items=line['items'][:4]
    for row,p in zip(items,(.5000,.5001,.5002,.5003)):
        requests.patch(base+f"/api/map-items/{row['id']}",json={'planned_start':p},timeout=10).raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        first=page.locator(f'#mapSvg [data-map-item="{items[0]["id"]}"] .map-stop-inner')
        second=page.locator(f'#mapSvg [data-map-item="{items[1]["id"]}"] .map-stop-inner')
        last=page.locator(f'#mapSvg [data-map-item="{items[3]["id"]}"] .map-stop-inner')
        x1,y1=_center(first);x2,_=_center(second);xl,yl=_center(last)
        page.mouse.move(xl,yl);page.mouse.down();page.mouse.move((x1+x2)/2,y1,steps=8);page.mouse.up();page.wait_for_timeout(700)
        x1b,_=_center(first);x2b,_=_center(second);xlb,_=_center(last)
        assert x1b < xlb < x2b,(x1b,xlb,x2b)
        updated=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();uline=next(row for row in updated['lines'] if row['id']==line['id']);by_id={row['id']:row for row in uline['items']}
        assert by_id[items[3]['id']]['planned_start'] < by_id[items[1]['id']]['planned_start']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
