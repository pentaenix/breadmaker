from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v813_recovery_modal_routes_to_project_settings_without_destructive_action(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.1.3 recovery settings'); page,errors=_page(base,scene_browser)
    try:
        page.evaluate("""()=>{
          const realApi=api;
          const recoveryWorkspace={
            recovery:{active_project_id:'broken',active_project_name:'Broken project',diagnostic:'test-diagnostic'},
            projects:[],
            recovery_candidates:[{id:'broken',path:'/tmp/broken',database_exists:true,readable:false}],
            trash:[],root:'/tmp'
          };
          state.workspace=recoveryWorkspace;
          api=async(path,opts)=>path==='/api/workspace'?recoveryWorkspace:realApi(path,opts);
        }""")
        page.evaluate("()=>openRecoveryBoot()")
        settings=page.locator('#recoveryProjectSettings'); settings.wait_for(); assert settings.is_visible()
        assert page.locator('#recoveryDeleteAll').count()==0
        settings.click()
        page.locator('#settingsDeleteAll').wait_for(); assert page.locator('#settingsDeleteAll').is_visible()
        assert page.locator('#settingsRecovery').is_visible()
        assert page.locator('#settingsBrowseProjects').is_visible()
        assert 'could not be opened safely' in page.locator('#modalBody').inner_text()
        page.locator('#settingsRecovery').click()
        page.locator('.recovery-backup-list').wait_for()
        assert page.locator('#recoveryCreateBackup').count()==0
        assert not errors,errors
    finally:_shutdown_page(page)
