from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v8185_brand_project_menu_and_settings_are_live(scene_live_server, scene_browser):
    base=scene_live_server
    _new_project(base,'8.18.5 Project Menu Audit')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#brandButton').click()
        page.locator('#projectMenu:not(.hidden)').wait_for(state='visible')
        assert page.locator('#projectMenuImport').is_visible()
        assert page.locator('#projectMenuExport').is_visible()
        assert page.locator('#projectMenuSettings').is_visible()

        page.locator('#projectMenuExport').click()
        page.locator('#projectSubmenu:not(.hidden)').wait_for(state='visible')
        assert page.locator('#projectMenuExportPdf').is_visible()
        # Reopen the top-level menu after the submenu interaction and enter Settings.
        page.locator('#brandButton').click()
        page.locator('#brandButton').click()
        page.locator('#projectMenuSettings').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")
        assert page.locator('#settingsMetadata').is_visible()
        assert page.locator('#settingsVerify').is_visible()
        assert page.locator('#settingsDeleteProject').is_visible()
        assert page.locator('#projectModal').count()==0
        assert not errors,errors
    finally:
        _shutdown_page(page)
