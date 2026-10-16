from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v600_project_menu_exposes_json_import_and_export(scene_live_server, scene_browser):
    # The historical browser harness intentionally bridges only string request bodies,
    # so multipart upload behavior is covered by TestClient in test_v600.py. This test
    # owns the real menu/UI discoverability contract.
    base=scene_live_server; _new_project(base,'6.0 JSON UI')
    page,errors=_page(base,scene_browser)
    try:
        page.click('#brandButton')
        page.hover('#projectMenuImport')
        page.wait_for_selector('#projectMenuImportJsonProject')
        assert page.locator('#projectMenuImportJsonProject').inner_text()=='Import JSON…'
        page.hover('#projectMenuExport')
        page.wait_for_selector('#projectMenuExportJsonProject')
        assert page.locator('#projectMenuExportJsonProject').inner_text()=='JSON Project'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
