from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v636-browser'},timeout=2)
    except Exception: pass


def test_v636_short_segments_remain_below_current_connector_threshold(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.6 shorter center connector')
    story=requests.post(base+'/api/maps',json={'name':'Compact Connector','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    compact=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Compact','planned_start':.20,'planned_end':.25,'schematic_start':.20,'schematic_end':.25},timeout=10).json()
    tiny=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Tiny','planned_start':.45,'planned_end':.47,'schematic_start':.45,'schematic_end':.47},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        assert page.locator(f'#mapSvg [data-map-line-connect-affordance="{compact["id"]}"]').count()==0
        assert page.locator(f'#mapSvg [data-map-line-connect-affordance="{tiny["id"]}"]').count()==0
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
