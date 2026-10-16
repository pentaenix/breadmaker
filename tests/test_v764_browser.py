from __future__ import annotations

import os
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

# This suite intentionally exercises a clean open-source install with no Google Client ID.
os.environ.pop('BREADMAKER_GOOGLE_CLIENT_ID', None)

CLIENT='v764-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v764_google_drive_stays_visible_and_keeps_user_owned_setup_minimal(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'User OAuth UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openBackups()');page.wait_for_selector('#backupAddDestination')
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        page.locator('[data-backup-kind="google_drive"]').click();page.wait_for_selector('#backupGoogleClientId')
        assert page.locator('#modalTitle').inner_text().strip() == 'Google Drive'
        assert page.locator('#modalBody input').count() == 2
        assert page.locator('#modalBody label').all_inner_texts() == ['OAuth Client ID','OAuth Client Secret']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
