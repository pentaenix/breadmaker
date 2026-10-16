from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v521_manuscript_import_uses_blank_fast_path_and_nonempty_destination_chooser(scene_live_server,scene_browser):
    base=scene_live_server
    _tab,chapter=_new_project(base,'5.2.1 Import choices')
    source=Path(tempfile.mkstemp(prefix='breadmaker-import-',suffix='.txt')[1])
    source.write_text('Imported manuscript',encoding='utf-8')
    page,errors=_page(base,scene_browser)
    try:
        # Default blank manuscript: no destructive-choice ceremony.
        page.locator('#importInput').set_input_files(str(source))
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Import Manuscript'")
        assert page.locator('#confirmImportManuscript').is_visible()
        assert page.locator('#confirmImportManuscript').inner_text().strip()=='Import here'
        assert page.locator('#importDestinationReplace').count()==0
        page.locator('#modalClose').click()
        page.locator('#importInput').set_input_files([])

        # Any real manuscript prose changes the same file chooser into an explicit
        # three-destination decision. The Replace branch adds a second confirmation.
        page.evaluate("""async cid=>{const data=await api(`/api/chapters/${cid}`);const b=data.blocks[0];await api(`/api/chapters/${cid}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:b.id,block_type:'paragraph',semantic_style:'body',content_html:'Existing manuscript prose',text_content:'Existing manuscript prose',image_path:'',scene_id:null}]})})}""",chapter['id'])
        page.locator('#importInput').set_input_files(str(source))
        page.wait_for_function("document.querySelector('#importDestinationReplace') && !document.querySelector('#modal').classList.contains('hidden')")
        assert page.locator('#importDestinationReplace').is_visible()
        assert page.locator('#importDestinationNew').is_visible()
        assert page.locator('#importDestinationTabs').is_visible()
        page.locator('#importDestinationReplace').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Replace Main Manuscript'")
        assert page.locator('#dialogConfirm').inner_text().strip()=='Replace Main Manuscript'
        assert 'verified recovery backup' in page.locator('#modalBody').inner_text().lower()
        page.locator('#dialogCancel').click()
        page.wait_for_function("document.querySelector('#importDestinationReplace') && !document.querySelector('#modal').classList.contains('hidden')")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page);source.unlink(missing_ok=True)
