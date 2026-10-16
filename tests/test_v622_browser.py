from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _menu_labels(page):
    return [x.strip() for x in page.locator('#contextMenu > .context-menu-row > button').all_inner_texts() if x.strip()]


def test_v622_canvas_and_map_collection_menus_match_click_and_right_click(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Collection menus 6.2.2')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click();page.wait_for_timeout(160)
        assert page.locator('#addCanvas').inner_text().strip()=='⋮'
        page.locator('#addCanvas').click();page.wait_for_timeout(50)
        click_labels=_menu_labels(page);assert click_labels==['Add Canvas','Import Canvas…']
        page.keyboard.press('Escape');page.wait_for_timeout(30)
        page.locator('.canvas-rail-head').click(button='right');page.wait_for_timeout(50)
        assert _menu_labels(page)==click_labels
        page.keyboard.press('Escape')

        page.locator('[data-view="maps"]').click();page.wait_for_timeout(160)
        assert page.locator('#newMapBtn').inner_text().strip()=='⋮'
        page.locator('#newMapBtn').click();page.wait_for_timeout(50)
        click_labels=_menu_labels(page);assert click_labels==['Add Map','Import Map…']
        page.keyboard.press('Escape');page.wait_for_timeout(30)
        page.locator('.map-tree-head').click(button='right');page.wait_for_timeout(50)
        assert _menu_labels(page)==click_labels
        assert not errors,errors
    finally:
        _shutdown_page(page)
