from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v631-browser'},timeout=2)
    except Exception: pass


def test_v631_line_overflow_menu_survives_async_rerender(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.1 line menu')
    first=requests.post(base+'/api/maps',json={'name':'First Map'},timeout=10).json()
    first_line=requests.post(base+f"/api/maps/{first['id']}/lines",json={'name':'First Line'},timeout=10).json()
    second=requests.post(base+'/api/maps',json={'name':'Second Map'},timeout=10).json()
    requests.post(base+f"/api/maps/{second['id']}/lines",json={'name':'Second Line'},timeout=10)

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{second["id"]}"]').click();page.wait_for_timeout(220)
        # Keep another Map expanded so clicking its Line menu must switch Maps and rerender the rail.
        page.evaluate("id=>{state.mapCollapsed.delete(id);renderMapTree();ensureMapTreeDetail(id).then(renderMapTree)}",first['id'])
        page.wait_for_function("id=>!!document.querySelector(`[data-map-tree-line='${id}']`)",arg=first_line['id'])
        page.wait_for_timeout(180)

        button=page.locator(f'[data-map-tree-line="{first_line["id"]}"] .map-tree-line-more')
        button.click();page.wait_for_timeout(220)
        assert page.locator('#contextMenu').is_visible()
        text=page.locator('#contextMenu').inner_text()
        assert 'Details' in text and 'Hide' in text and 'Delete' in text
        assert 'Focus' not in text and 'Color' not in text and 'Populate' not in text
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
