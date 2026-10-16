from __future__ import annotations
import json
from pathlib import Path
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

ROOT=Path(__file__).resolve().parents[1]


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _open_language(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuLanguage').click()
    page.locator('#modal').wait_for(state='visible')


def test_v701_language_picker_search_switch_and_persistence(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Language Picker 7.0.1')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(250)
        _open_language(page)
        search=page.locator('#languageSearch')
        search.fill('span')
        assert page.locator('[data-language-code="es"]').count()==1
        search.fill('espa')
        assert page.locator('[data-language-code="es"]').count()==1
        search.fill('does-not-exist')
        assert page.locator('.language-picker-empty').count()==1
        search.fill('espa')
        page.locator('[data-language-code="es"]').click()
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.lang")=='es'
        assert page.evaluate("localStorage.getItem('breadmaker-language')")=='es'
        assert page.locator('[data-view="tasks"]').inner_text()=='Tareas'
        # Selection closes the picker and persists the newly selected locale.
        assert not page.locator('#modal').is_visible()
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
