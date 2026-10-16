from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v6318_map_background_custom_default_custom_has_no_reference_error(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.18 background refresh')
    story=requests.post(base+'/api/maps',json={'name':'Background Map'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(180)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(80)
        page.locator('#editMapDetailsBtn').click();page.wait_for_timeout(80)
        mode=page.locator('#mapDetailsBackgroundMode')
        text=page.locator('#mapDetailsBackgroundText')
        mode.select_option('custom');page.wait_for_timeout(30)
        text.fill('#123456');text.press('Tab');page.wait_for_timeout(220)
        assert requests.get(base+f'/api/maps/{story["id"]}',timeout=10).json()['map']['background'].lower()=='#123456'
        mode.select_option('default');page.wait_for_timeout(220)
        assert requests.get(base+f'/api/maps/{story["id"]}',timeout=10).json()['map']['background'].lower()=='#f3f3f1'
        mode.select_option('custom');page.wait_for_timeout(30)
        text.fill('#654321');text.press('Tab');page.wait_for_timeout(220)
        assert requests.get(base+f'/api/maps/{story["id"]}',timeout=10).json()['map']['background'].lower()=='#654321'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
