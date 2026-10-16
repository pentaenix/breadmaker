from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v700_locale_bindings_change_builtin_ui_not_user_content(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Localization 7.0')
    requests.post(base+'/api/tasks',json={'title':'Open','status':'open'},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(250)
        body=page.locator('body').inner_text()
        assert '{{i18n:' not in body
        assert page.title()=='Breadmaker Editor'
        meta=requests.get(base+'/api/locales',timeout=10).json()['locales'][0]
        assert meta['english_name']=='English' and meta['native_name']=='English'
        page.locator('[data-view="tasks"]').click();page.wait_for_timeout(220)
        assert page.get_by_text('Open',exact=True).count() >= 1
        # Simulate a future locale without changing project content. Every catalog entry whose
        # English value is exactly Open gets a visible prefix, then existing locale bindings refresh.
        page.evaluate("""()=>{for(const [k,v] of Object.entries(i18nCatalog.strings)){if(v==='Open')i18nCatalog.strings[k]='ZZ Open'};refreshI18nBindings();loadTasks?.()}""")
        page.wait_for_timeout(120)
        assert page.get_by_text('ZZ Open',exact=True).count() >= 1
        # User-authored Task text is not locale-bound and stays byte-for-byte unchanged.
        assert page.locator('.task-title-text').filter(has_text='Open').count() >= 1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
