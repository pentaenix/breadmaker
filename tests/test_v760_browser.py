from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page
from test_v760 import _dav_server

CLIENT='v41812-browser'

def _release(base):
    try:requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception:pass


def test_v760_direct_remote_ui_connects_and_states_verification_boundary(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.6 Direct Remote UI')
    server,state,endpoint=_dav_server()
    try:
        added=requests.post(base+'/api/backup-destinations/webdav',json={'endpoint':endpoint,'username':'writer','password':'app-pass'},timeout=30)
        assert added.status_code==200,added.text
        destination_id=added.json()['destination']['id']
        caught=requests.post(base+'/api/backup-destinations/catch-up',timeout=60)
        assert caught.status_code==200,caught.text

        page,errors=_page(base,scene_browser)
        try:
            page.evaluate('openBackups()');page.wait_for_selector('.backup-destination-row')
            row=page.locator(f'[data-backup-destination="{destination_id}"]')
            text=row.inner_text()
            assert 'WebDAV / Nextcloud' in text
            assert 'Up to date' in text
            assert 'Last remote copy verified' in text

            page.locator('#backupAddDestination').click();page.wait_for_selector('[data-backup-kind="webdav"]')
            chooser=page.locator('#modalBody').inner_text()
            assert 'Folder or removable drive' in chooser and 'Synced folder' in chooser and 'Google Drive' in chooser and 'WebDAV / Nextcloud' in chooser
            page.locator('[data-backup-kind="webdav"]').click();page.wait_for_selector('#backupRemoteEndpoint')
            connect=page.locator('#modalBody').inner_text()
            assert 'WebDAV URL' in connect and 'Username' in connect and 'Password or app password' in connect
            assert 'Breadmaker uploads, downloads, and verifies its own backup packages there.' not in connect
            assert 'HTTPS is required except for localhost.' not in connect
            page.evaluate('closeModal(false)')

            page.evaluate('openBackups()');page.wait_for_selector('.backup-destination-row')
            page.locator(f'[data-backup-destination="{destination_id}"]').click();page.wait_for_selector('#backupDestinationChange')
            body=page.locator('#modalBody').inner_text()
            assert 'Breadmaker connects directly to this remote server.' in body
            assert 'downloads the finished remote package and verifies it' in body
            assert 'fully verifies the finished remote copy' in body
            assert page.locator('#backupDestinationKind').count()==0
            assert 'Reconnect…' in page.locator('#backupDestinationChange').inner_text()
            assert page.locator('[data-destination-backup]').count()>=1
            page.locator('#backupDestinationChange').click();page.wait_for_selector('#backupRemoteEndpoint')
            assert page.locator('#backupRemoteEndpoint').input_value()==endpoint
            assert not errors,errors
        finally:
            _release(base);_shutdown_page(page)
    finally:
        server.shutdown();server.server_close()
