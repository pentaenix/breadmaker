from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _source_file():
    source=Path(tempfile.mkstemp(prefix='breadmaker-import-progress-',suffix='.txt')[1])
    source.write_text('Imported manuscript with visible progress',encoding='utf-8')
    return source


def test_v522_import_prompt_has_cancel_and_stays_visible_with_progress_until_success(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'5.2.2 Import progress')
    source=_source_file()
    page,errors=_page(base,scene_browser)
    try:
        # Cancel is a real pre-import action and dismissing the prompt clears the picker.
        page.locator('#importInput').set_input_files(str(source))
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Import Manuscript'")
        assert page.locator('#cancelImportManuscript').is_visible()
        assert page.locator('#cancelImportManuscript').is_enabled()
        page.locator('#cancelImportManuscript').click()
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")

        # Reopen the same source and hold the import primitive long enough to inspect
        # the progress lifecycle without changing the server/import parser itself.
        page.locator('#importInput').set_input_files(str(source))
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Import Manuscript'")
        page.evaluate("""() => {
          performManuscriptImport=async()=>{await new Promise(resolve=>setTimeout(resolve,900));return {chapters:1,images:0}};
        }""")
        page.evaluate("document.querySelector('#confirmImportManuscript').click()")
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Importing Manuscript'")
        assert page.locator('#modal').is_visible()
        assert page.locator('#manuscriptImportProgressBar').is_visible()
        assert page.locator('#manuscriptImportProgressStatus').is_visible()
        assert page.locator('#cancelImportManuscript').is_disabled()
        assert page.locator('#modalClose').is_disabled()

        # A progress modal cannot be dismissed by clicking the dimmed backdrop.
        page.evaluate("document.querySelector('#modal').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        assert page.locator('#modal').is_visible()
        page.wait_for_function("Number.parseFloat(document.querySelector('#manuscriptImportProgressBar')?.style.width||'0') > 6")
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')",timeout=5000)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page);source.unlink(missing_ok=True)


def test_v522_failed_import_stays_visible_with_error_and_close_action(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'5.2.2 Import failure visibility')
    source=_source_file()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#importInput').set_input_files(str(source))
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Import Manuscript'")
        page.evaluate("""() => { performManuscriptImport=async()=>{await new Promise(resolve=>setTimeout(resolve,120));throw new Error('Could not read this document')}; }""")
        page.evaluate("document.querySelector('#confirmImportManuscript').click()")
        page.wait_for_function("document.querySelector('#closeFailedImport')")
        assert page.locator('#modal').is_visible()
        assert 'Import could not be completed' in page.locator('#modalBody').inner_text()
        assert 'Could not read this document' in page.locator('#modalBody').inner_text()
        assert page.locator('#modalClose').is_enabled()
        page.locator('#closeFailedImport').click()
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page);source.unlink(missing_ok=True)
