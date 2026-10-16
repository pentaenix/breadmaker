from __future__ import annotations
import json
from pathlib import Path
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

ROOT=Path(__file__).resolve().parents[1]


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v702_language_is_directly_below_project_settings_and_not_inside_settings(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Language Placement 7.0.2')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(250)
        page.locator('#brandButton').click()
        settings=page.locator('#projectMenuSettings')
        language=page.locator('#projectMenuLanguage')
        assert settings.count()==1 and language.count()==1
        order=page.locator('#projectMenu > .project-menu-item').evaluate_all('(els)=>els.map(e=>e.id)')
        assert order.index('projectMenuLanguage')==order.index('projectMenuSettings')+1
        language.click()
        page.locator('#modal').wait_for(state='visible')
        assert page.locator('#languageSearch').count()==1
        page.locator('#modalClose').click()
        page.locator('#brandButton').click()
        page.locator('#projectMenuSettings').click()
        page.locator('#modal').wait_for(state='visible')
        assert page.locator('#settingsLanguage').count()==0
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v702_language_search_still_matches_english_and_native_names(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Language Search 7.0.2')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(250)
        page.locator('#brandButton').click()
        page.locator('#projectMenuLanguage').click()
        search=page.locator('#languageSearch')
        search.fill('span')
        assert page.locator('[data-language-code="es"]').count()==1
        search.fill('espa')
        assert page.locator('[data-language-code="es"]').count()==1
        page.locator('[data-language-code="es"]').click()
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.lang")=='es'
        assert page.evaluate("localStorage.getItem('breadmaker-language')")=='es'
        assert page.locator('[data-view="tasks"]').inner_text()=='Tareas'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
