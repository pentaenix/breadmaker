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


def test_v741_backups_ui_discovers_other_project_on_destination(scene_live_server,scene_browser):
    base=scene_live_server
    original_name='Surviving Novel 7.4.1';_new_project(base,original_name)
    parent=Path(tempfile.mkdtemp(prefix='breadmaker-v741-recovery-'));usb=parent/'Writing USB';usb.mkdir()
    try:
        added=requests.post(base+'/api/backup-destinations',json={'path':str(usb)},timeout=30)
        assert added.status_code==200,added.text
        destination_id=added.json()['destination']['id']
        caught=requests.post(base+'/api/backup-destinations/catch-up',timeout=30)
        assert caught.status_code==200,caught.text
        assert list(usb.glob('*.breadmaker'))

        # Make a new blank project the active project, then attach the surviving
        # destination there. Recovery discovery must not be scoped to this new id.
        blank=requests.post(base+'/api/projects',json={'name':'Replacement Blank'},timeout=30)
        assert blank.status_code==200,blank.text
        attached=requests.post(base+'/api/backup-destinations',json={'path':str(usb)},timeout=30)
        assert attached.status_code==200,attached.text
        replacement_destination=attached.json()['destination']['id']
        assert replacement_destination!=destination_id

        page,errors=_page(base,scene_browser)
        try:
            page.evaluate('openBackups()');page.wait_for_selector('.backup-destination-row')
            page.locator(f'[data-backup-destination="{replacement_destination}"]').click()
            page.wait_for_selector('.backup-recovery-project')
            body=page.locator('#modalBody').inner_text()
            assert 'Recoverable projects on this destination' in body
            assert original_name in body
            assert 'Will be verified before restore' in body
            assert page.locator('[data-destination-backup]').count()>=1
            assert not errors,errors
        finally:
            _release(base);_shutdown_page(page)
    finally:
        shutil.rmtree(parent,ignore_errors=True)
