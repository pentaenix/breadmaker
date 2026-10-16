from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _switch_spanish(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuLanguage').click()
    page.locator('#languageSearch').fill('espa')
    page.locator('[data-language-code="es"]').click()
    page.wait_for_timeout(150)
    assert page.evaluate('document.documentElement.lang')=='es'


def test_v704_runtime_localization_review(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Localization Review 7.0.4')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(300)
        _switch_spanish(page)

        # Stateful top/bottom status and word count.
        assert page.locator('#saveState').inner_text()=='Guardado'
        assert page.locator('#globalSaveState').inner_text()=='Guardado'
        page.evaluate("setSaveState('Saving…')")
        assert page.locator('#saveState').inner_text()=='Guardando…'
        assert page.locator('#globalSaveState').inner_text()=='Guardando…'
        page.evaluate("setSaveState('Saved')")
        assert page.locator('#saveState').inner_text()=='Guardado'
        assert page.locator('#globalSaveState').inner_text()=='Guardado'
        assert page.locator('#wordCount').inner_text().endswith('palabras')

        # Initial Project dropdown and Project Settings are localized.
        page.locator('#brandButton').click()
        assert page.locator('#projectMenuSettings').inner_text()=='Configuración del proyecto…'
        page.locator('#projectMenuSettings').click()
        assert page.locator('#modalTitle').inner_text()=='Configuración del proyecto'
        assert page.locator('#settingsMetadata b').inner_text()=='Metadatos'
        page.locator('#modalClose').click()

        # Editor's Eye active tooltip/state must update after JS state changes.
        page.locator('#editorsEyeBtn').click()
        page.wait_for_timeout(350)
        assert page.locator('#editorsEyeTooltipStatus').inner_text().startswith('Activado')
        assert 'Ojo del editor activado' in page.locator('#editorsEyeBtn').get_attribute('aria-label')
        page.locator('#editorsEyeBtn').click()

        # Find scope must be localized in both whole-book and focused-chapter views.
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click()
        assert page.locator('#findScopeLabel').inner_text()=='Libro'
        page.locator('.chapter-row').first.dblclick()
        page.wait_for_timeout(200)
        assert page.locator('#findScopeLabel').inner_text()=='Capítulo'

        # Bookmark tooltip content is refreshed through the runtime localization path.
        assert page.locator('#bookmarkBtn').get_attribute('aria-label')=='Ir al marcapáginas'
        assert page.locator('#bookmarkTooltipText').text_content()=='Haz clic para ir a tu marcador del manuscrito.'

        # User-reported Cuts empty state.
        page.locator('#cutsBtn').click()
        page.wait_for_timeout(100)
        assert page.locator('#cutsList .cuts-empty b').inner_text()=='Aún no hay recortes'
        assert page.locator('#cutsList .cuts-empty span').inner_text().startswith('Selecciona texto del manuscrito')

        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
