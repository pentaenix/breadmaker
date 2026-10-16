from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v766-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v766_google_secret_is_masked_never_prefilled_and_cleared_on_submit(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6.6 Google Secret UI')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openBackups()');page.wait_for_selector('#backupAddDestination')
        page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="google_drive"]')
        page.locator('[data-backup-kind="google_drive"]').click();page.wait_for_selector('#backupGoogleClientSecret')
        assert page.locator('#modalTitle').inner_text().strip()=='Google Drive'
        assert page.locator('#modalBody label').all_inner_texts()==['OAuth Client ID','OAuth Client Secret']
        secret=page.locator('#backupGoogleClientSecret')
        assert secret.get_attribute('type')=='password'
        assert secret.get_attribute('autocomplete')=='off'
        assert secret.input_value()==''
        assert 'Google Cloud' not in page.locator('#modalBody').inner_text()
        page.evaluate("window.open=()=>({close(){}})")
        page.locator('#backupGoogleClientId').fill('writer.apps.googleusercontent.com')
        secret.fill('GOCSPX-browser-secret')
        page.locator('#backupGoogleConnect').click()
        page.wait_for_function("document.querySelector('#backupGoogleStatus')?.textContent.includes('Finish in Google')")
        assert secret.input_value()==''
        config=requests.get(base+'/api/backup-destinations/google-drive/config',timeout=3).json()
        assert config=={'configured':True,'client_id':'writer.apps.googleusercontent.com'}
        assert set(config)=={'configured','client_id'}
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
