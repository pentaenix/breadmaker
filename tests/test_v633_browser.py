from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v633-browser'},timeout=2)
    except Exception: pass


def test_v633_minimal_labels_share_one_automatic_level(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.3 minimal labels')
    story=requests.post(base+'/api/maps',json={'name':'Label Alignment','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Main'},timeout=10).json()
    for idx,title in enumerate(('First label','Second label with much longer text','Third label','Fourth label')):
        requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':title,'planned_start':.40+idx*.002,'schematic_start':.40+idx*.002},timeout=10)

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(180)
        values=page.evaluate("""() => [...document.querySelectorAll('#mapSvg .map-item-label.map-label-minimal:not(.pinned)')].map(label=>({offset:+label.dataset.labelOffset,baseY:+label.dataset.labelBaseY,y:+label.getAttribute('y')}))""")
        assert len(values)==4
        assert {row['offset'] for row in values}=={-34}
        assert all(abs((row['baseY']-34)-row['y'])<.001 for row in values)

        # Full mode remains free to stagger labels for collision avoidance.
        page.evaluate("""async()=>{await api(`/api/maps/${state.currentMap.id}`,{method:'PATCH',body:JSON.stringify({label_mode:'full'})});await reloadCurrentMap()}""")
        page.wait_for_timeout(180)
        full_offsets=page.evaluate("""() => [...document.querySelectorAll('#mapSvg .map-item-label.map-label-full:not(.pinned)')].map(label=>+label.dataset.labelOffset)""")
        assert len(full_offsets)==4
        assert len(set(full_offsets))>1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
