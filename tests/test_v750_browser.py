from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v41812-browser'


def _release(base):
    try:requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception:pass


def test_v750_synced_folder_ui_is_explicit_about_local_verification(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.5 Synced Folder UI')
    parent=Path(tempfile.mkdtemp(prefix='breadmaker-v750-sync-'));folder=parent/'Synced Folder';folder.mkdir()
    try:
        added=requests.post(base+'/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'},timeout=30)
        assert added.status_code==200,added.text
        destination_id=added.json()['destination']['id']
        caught=requests.post(base+'/api/backup-destinations/catch-up',timeout=30)
        assert caught.status_code==200,caught.text

        page,errors=_page(base,scene_browser)
        try:
            page.evaluate('openBackups()');page.wait_for_selector('.backup-destination-row')
            row=page.locator(f'[data-backup-destination="{destination_id}"]')
            assert 'Synced folder' in row.inner_text()
            assert 'Local copy verified' in row.inner_text()

            # Add Destination first asks what kind of filesystem destination this is;
            # it must not jump straight to a provider-branded cloud flow.
            page.locator('#backupAddDestination').click()
            page.wait_for_selector('[data-backup-kind="synced_folder"]')
            chooser=page.locator('#modalBody').inner_text()
            assert 'Folder or removable drive' in chooser
            assert 'Synced folder' in chooser
            assert 'iCloud Drive' in chooser and 'Dropbox' in chooser and 'Google Drive' in chooser and 'OneDrive' in chooser
            page.evaluate('closeModal(false)')

            row=page.locator(f'[data-backup-destination="{destination_id}"]');row.click()
            page.wait_for_selector('#backupDestinationKind')
            body=page.locator('#modalBody').inner_text()
            assert 'Destination type' in body
            assert 'Synced folder' in body
            assert 'Breadmaker verifies the copy stored in this folder. Your sync service is responsible for uploading it.' in body
            assert 'Breadmaker verifies this folder copy only. Remote sync status is managed by your sync service.' in body
            assert 'cloud backup verified' not in body.lower()
            assert 'remote copy verified' not in body.lower()

            # Reclassification changes only the semantics label; the same verified
            # filesystem bytes remain current and no provider changes underneath.
            page.locator('#backupDestinationKind').click()
            page.wait_for_selector('[data-backup-kind="local"]')
            page.locator('[data-backup-kind="local"]').click()
            page.wait_for_function("document.querySelector('#modalBody')?.innerText.includes('Folder or removable drive')")
            body=page.locator('#modalBody').inner_text()
            assert 'Folder or removable drive' in body
            assert 'responsible for uploading it' not in body
            assert not errors,errors
        finally:
            _release(base);_shutdown_page(page)
    finally:
        shutil.rmtree(parent,ignore_errors=True)
