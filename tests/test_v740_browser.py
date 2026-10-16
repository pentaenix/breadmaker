from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v41812-browser'

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v740_backups_ui_stays_compact_with_multiple_destinations(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.4 destinations UI')
    roots=[]
    try:
        for name in ('Writing USB','Desk SSD'):
            folder=Path(tempfile.mkdtemp(prefix='breadmaker-v740-destination-'))/name
            folder.mkdir();roots.append(folder.parent)
            response=requests.post(base+'/api/backup-destinations',json={'path':str(folder)},timeout=30)
            assert response.status_code==200,response.text
        caught=requests.post(base+'/api/backup-destinations/catch-up',timeout=30)
        assert caught.status_code==200,caught.text
        page,errors=_page(base,scene_browser)
        try:
            page.evaluate('openProjectSettings()');page.wait_for_selector('#settingsBackups')
            assert page.locator('#settingsBackups b').inner_text()=='Backup locations'
            page.evaluate('openBackups()');page.wait_for_selector('.backup-destination-row')
            assert page.locator('.backup-destination-row').count()==2
            assert page.locator('#backupAddDestination').inner_text()=='+ Add Destination'
            first=page.locator('.backup-destination-row').first
            assert 'Up to date' in first.inner_text()
            first.click();page.wait_for_selector('#backupDestinationNow')
            assert page.locator('#backupDestinationChange').inner_text()=='Change Folder…'
            assert page.locator('#backupDestinationNow').inner_text()=='Back Up Now'
            assert page.locator('.recovery-backup-row').count()>=1
            assert 'Will be verified before restore' in page.locator('.recovery-backup-row').first.inner_text()
            assert not errors,errors
        finally:
            _release(base);_shutdown_page(page)
    finally:
        for root in roots:shutil.rmtree(root,ignore_errors=True)
