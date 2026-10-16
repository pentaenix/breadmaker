from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v763-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v763_backup_locations_order_google_visible_and_webdav_minimal(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6.3 Backup locations UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openProjectSettings()');page.wait_for_selector('#settingsBackups')
        assert page.locator('#settingsBackups b').inner_text().strip() == 'Backup locations'
        assert page.locator('#settingsRecovery b').inner_text().strip() == 'Recovery Backups'
        order=page.locator('#settingsBackups, #settingsRecovery').evaluate_all('(els)=>els.map(e=>e.id)')
        assert order == ['settingsBackups','settingsRecovery']

        page.locator('#settingsBackups').click();page.wait_for_selector('#backupAddDestination')
        assert page.locator('#modalTitle').inner_text().strip() == 'Backup locations'
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        assert page.locator('[data-backup-kind="google_drive"]').is_visible()

        page.locator('[data-backup-kind="webdav"]').click();page.wait_for_selector('#backupRemoteEndpoint')
        body=page.locator('#modalBody').inner_text()
        assert 'Enter the URL of a dedicated WebDAV folder' not in body
        assert page.locator('#backupRemoteEndpoint').count()==1
        assert page.locator('#backupRemoteUsername').count()==1
        assert page.locator('#backupRemotePassword').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
