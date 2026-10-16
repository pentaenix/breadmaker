from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v41812-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def test_v731_project_settings_recovery_and_backup_entries_are_compact(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'7.3.1 settings ui')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openProjectSettings()')
        page.wait_for_selector('#settingsRecovery')
        values=page.evaluate("""()=>({
          old:document.querySelectorAll('#settingsBackup').length,
          recovery:document.querySelector('#settingsRecovery b')?.textContent?.trim(),
          backups:document.querySelector('#settingsBackups b')?.textContent?.trim()
        })""")
        assert values=={'old':0,'recovery':'Recovery Backups','backups':'Backup locations'}
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v731_recovery_list_is_scrollable_and_hides_package_filename(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'7.3.1 recovery list ui')
    created=requests.post(base+'/api/backup',timeout=8)
    assert created.status_code==200,created.text
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openRecovery()')
        page.wait_for_selector('.recovery-backup-row')
        values=page.evaluate("""()=>({
          overflow:getComputedStyle(document.querySelector('.recovery-backup-list')).overflowY,
          title:document.querySelector('.recovery-backup-copy b')?.textContent||'',
          create:document.querySelector('#recoveryCreateBackup')?.textContent?.trim()
        })""")
        assert values['overflow']=='auto'
        assert '.breadmaker' not in values['title']
        assert values['create']=='Create Backup'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v731_backup_locations_surface_uses_add_destination(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'7.3.1 backup locations ui')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('renderBackups([])')
        page.wait_for_selector('#backupAddDestination')
        values=page.evaluate("""()=>({
          legacy:document.querySelectorAll('#externalBackupPath').length,
          add:document.querySelector('#backupAddDestination')?.textContent?.trim()
        })""")
        assert values=={'legacy':0,'add':'+ Add Destination'}
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
