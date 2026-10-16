from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v765-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v765_google_drive_modal_is_minimal_but_configurable(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6.5 Google OAuth UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openBackups()');page.wait_for_selector('#backupAddDestination')
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        page.locator('[data-backup-kind="google_drive"]').click();page.wait_for_selector('#backupGoogleClientId')
        assert page.locator('#modalTitle').inner_text().strip()=='Google Drive'
        assert page.locator('#modalBody label').all_inner_texts()==['OAuth Client ID','OAuth Client Secret']
        assert page.locator('#modalBody input').count()==2
        assert page.locator('#backupGoogleClientSecret').get_attribute('type')=='password'
        assert 'Google Cloud' not in page.locator('#modalBody').inner_text()
        assert 'API' not in page.locator('#modalBody').inner_text()
        page.evaluate("window.open=()=>({close(){}})")
        page.locator('#backupGoogleClientId').fill('writer.apps.googleusercontent.com')
        page.locator('#backupGoogleClientSecret').fill('GOCSPX-test-secret')
        page.locator('#backupGoogleConnect').click()
        page.wait_for_function("document.querySelector('#backupGoogleStatus')?.textContent.includes('Finish in Google')")
        config=requests.get(base+'/api/backup-destinations/google-drive/config',timeout=3).json()
        assert config=={'configured':True,'client_id':'writer.apps.googleusercontent.com'}
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
