from __future__ import annotations

import os
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


CLIENT='v761-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v761_google_drive_ui_uses_oauth_not_password_or_api_key(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6 Google Drive UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openBackups()');page.wait_for_selector('#backupAddDestination')
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        chooser=page.locator('#modalBody').inner_text()
        assert 'Google Drive' in chooser and 'WebDAV / Nextcloud' in chooser
        page.locator('[data-backup-kind="google_drive"]').click();page.wait_for_selector('#backupGoogleConnect')
        body=page.locator('#modalBody').inner_text()
        assert page.locator('#backupGoogleConnect').inner_text().strip() in {'Connect','Reconnect'}
        assert 'OAuth Client ID' in body and 'OAuth Client Secret' in body and 'API key' not in body and 'service account' not in body.lower()
        assert page.locator('#backupGoogleClientId').count()==1 and page.locator('#backupGoogleClientSecret').count()==1
        assert page.locator('input[type="password"]').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
