from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v6316_maps_rail_background_opens_collection_menu_but_map_row_keeps_map_menu(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.16 rail context')
    story=requests.post(base+'/api/maps',json={'name':'Story Map','layout_mode':'scaled','label_mode':'minimal','ruler':'none'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        row=page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]')
        row.wait_for()

        rail=page.locator('#mapRail').bounding_box(); assert rail
        tree=page.locator('#mapTree').bounding_box(); assert tree
        # Right-click empty rail space below the final row.
        x=rail['x']+rail['width']/2
        y=min(rail['y']+rail['height']-8,tree['y']+tree['height']-8)
        page.mouse.click(x,y,button='right');page.wait_for_timeout(100)
        menu=page.locator('.context-menu')
        assert menu.is_visible()
        text=menu.inner_text()
        assert 'Add Map' in text
        assert 'Import Map' in text
        page.keyboard.press('Escape')

        row.click(button='right');page.wait_for_timeout(100)
        assert menu.is_visible()
        text=menu.inner_text()
        assert 'New Line' in text
        assert 'Add Map' not in text
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
