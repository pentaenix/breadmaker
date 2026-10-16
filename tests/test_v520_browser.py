from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v520_project_menu_preserves_every_project_center_capability_and_logo_contract(scene_live_server,scene_browser):
    base=scene_live_server
    _tab,_chapter=_new_project(base,'5.2 Project menu primary')
    second=requests.post(base+'/api/projects',json={'name':'5.2 Other Project'},timeout=10)
    second.raise_for_status()
    # Return to the project the browser was created for so the switch submenu has another destination.
    projects=requests.get(base+'/api/workspace',timeout=10).json()['projects']
    primary=next(p for p in projects if p['name']=='5.2 Project menu primary')
    requests.post(base+f"/api/projects/{primary['id']}/activate",timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        # Logo is transparent in both themes; only the border/B color flips.
        light=page.locator('.brand-mark').evaluate("el=>{const s=getComputedStyle(el);return [s.backgroundColor,s.color,s.borderTopColor]}")
        assert light[0] in ('rgba(0, 0, 0, 0)','transparent')
        assert light[1]==light[2]
        page.evaluate("document.documentElement.dataset.theme='dark'")
        dark=page.locator('.brand-mark').evaluate("el=>{const s=getComputedStyle(el);return [s.backgroundColor,s.color,s.borderTopColor]}")
        assert dark[0] in ('rgba(0, 0, 0, 0)','transparent')
        assert dark[1]==dark[2]
        page.evaluate("document.documentElement.dataset.theme='light'")

        page.locator('#brandButton').click()
        page.locator('#projectMenu').wait_for(state='visible')
        assert page.locator('#projectModal').count()==0
        assert page.locator('#projectMenuNew').is_visible()
        assert page.locator('#projectMenuSwitch').is_visible()
        assert page.locator('#projectMenuImport').is_visible()
        assert page.locator('#projectMenuExport').is_visible()
        assert page.locator('#projectMenuSettings').is_visible()
        assert page.locator('#projectMenuRename').is_visible()

        # Rename is inline beside the current project name, not a primary modal action.
        page.locator('#projectMenuRename').click()
        rename=page.locator('#projectMenuRenameInput'); rename.wait_for(state='visible')
        rename.fill('5.2 Renamed Project'); rename.press('Enter')
        page.wait_for_function("document.querySelector('#projectMenuProjectName')?.textContent==='5.2 Renamed Project'")
        workspace=requests.get(base+'/api/workspace',timeout=10).json()
        assert next(p for p in workspace['projects'] if p['id']==workspace['active_project_id'])['name']=='5.2 Renamed Project'

        # Switch keeps both quick switching and the old Browse All capability.
        page.locator('#projectMenuSwitch').hover()
        page.locator('#projectSubmenu').wait_for(state='visible')
        assert page.locator('[data-switch-project]').count()>=1
        assert page.locator('#projectMenuBrowseAll').is_visible()

        # Import/export actions are grouped without losing any of the old routes.
        page.locator('#projectMenuImport').hover()
        assert page.locator('#projectMenuImportManuscript').is_visible()
        assert page.locator('#projectMenuImportProject').is_visible()
        assert page.locator('#projectMenuImportCuts').is_visible()
        page.locator('#projectMenuExport').hover()
        assert page.locator('#projectMenuExportDocx').is_visible()
        assert page.locator('#projectMenuExportPdf').is_visible()
        assert page.locator('#projectMenuExportProject').is_visible()
        assert page.locator('#projectMenuExportCuts').is_visible()

        page.locator('#projectMenuSettings').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")
        for selector in (
            '#settingsRenameProject','#settingsMetadata','#settingsHistory','#settingsBrowseProjects',
            '#settingsBackup','#settingsRecovery','#settingsVerify','#settingsDeleteProject','#settingsDeleteAll',
        ):
            assert page.locator(selector).is_visible(),selector

        # Administrative children return to Settings instead of rebuilding the former Project Center.
        page.locator('#settingsMetadata').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Metadata'")
        page.locator('#modalClose').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings' && !document.querySelector('#modal').classList.contains('hidden')")
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
