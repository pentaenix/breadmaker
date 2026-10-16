from __future__ import annotations

import os
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


CLIENT='v762-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v762_google_drive_connection_is_minimal_and_writer_has_no_cloud_console_setup(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6.2 Google Drive UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openBackups()');page.wait_for_selector('#backupAddDestination')
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        google=page.locator('[data-backup-kind="google_drive"]')
        assert 'Google Drive' in google.inner_text()
        assert 'Back up directly to Google Drive' in google.inner_text()
        google.click();page.wait_for_selector('#backupGoogleConnect')
        assert page.locator('#modalTitle').inner_text().strip() == 'Google Drive'
        assert page.locator('#backupGoogleConnect').inner_text().strip() == 'Connect'
        body=page.locator('#modalBody').inner_text().strip()
        assert body == 'OAuth Client ID\nOAuth Client Secret'
        assert page.locator('#backupGoogleClientId').count() == 1 and page.locator('#backupGoogleClientSecret').count() == 1
        html=page.locator('#modal').inner_text()
        for forbidden in ('Google Cloud','API key','service account','Google password'):
            assert forbidden.lower() not in html.lower()
        assert page.locator('#modalBody input').count()==2
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
