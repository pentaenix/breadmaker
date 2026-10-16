from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v800_reviews_tab_remains_a_routed_top_level_workspace(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'8.0.0 Reviews shell')
    page,errors=_page(base,scene_browser)
    try:
        tab=page.locator('[data-view="reviews"]')
        assert tab.inner_text()=='Reviews'
        tab.click()
        page.wait_for_function("document.querySelector('#reviewsView').classList.contains('active')")
        assert page.locator('#reviewsView').inner_text().strip()!=''
        assert not page.locator('#manuscriptView').get_attribute('class').endswith('active')

        page.locator('[data-view="manuscript"]').click()
        page.wait_for_function("document.querySelector('#manuscriptView').classList.contains('active')")
        tab.click()
        page.wait_for_function("document.querySelector('#reviewsView').classList.contains('active')")
        assert page.locator('#reviewsView').inner_text().strip()!=''
        assert not errors,errors
    finally:
        _shutdown_page(page)
